import telebot
from telebot import types
import time
import html
import os
import re
import threading
import datetime
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
import psycopg2 # Драйвер для Postgres

# --- КОНФИГУРАЦИЯ ---

PROJECT_NAME = os.getenv('PROJECT_NAME', 'VPN Support')
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', '0'))
BANS_TOPIC_ID = int(os.getenv('BANS_TOPIC_ID', '1'))
AUTO_CLOSE_HOURS = int(os.getenv('AUTO_CLOSE_HOURS', '24'))

# Новые переменные для отзывов
REVIEWS_TOPIC_ID = int(os.getenv('REVIEWS_TOPIC_ID', '1'))
REVIEWS_CHANNEL = os.getenv('REVIEWS_CHANNEL', '@my_reviews_channel')

PG_HOST = os.getenv('PG_HOST', 'remnawave_bot_db')
PG_DB = os.getenv('PG_DB', 'remnawave_bot')
PG_USER = os.getenv('PG_USER', 'remnawave_user')
PG_PASS = os.getenv('PG_PASS', '')

# Локальная БД саппорта (для тикетов, банов и состояний)
DB_PATH = "support.db"
LOG_PATH = os.getenv('LOG_PATH', 'bot.log')
db_lock = threading.Lock()

# --- ЛОГИРОВАНИЕ ---
# Пишем одновременно в stdout (видно через `docker compose logs -f -t`)
# и в файл с ротацией по размеру в папке с ботом (5 файлов по 5 МБ).
logger = logging.getLogger("support_bot")
logger.setLevel(logging.INFO)
_log_fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

_stream_handler = logging.StreamHandler()  # -> stdout, попадает в docker logs
_stream_handler.setFormatter(_log_fmt)
logger.addHandler(_stream_handler)

_file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
_file_handler.setFormatter(_log_fmt)
logger.addHandler(_file_handler)

# Приглушаем шумный INFO от библиотеки telebot, но оставляем предупреждения/ошибки.
logging.getLogger("TeleBot").setLevel(logging.WARNING)

def _strip_html(text):
    """Убирает HTML-теги, чтобы лог в файле/консоли читался без разметки."""
    return re.sub(r"<[^>]+>", "", text)

bot = telebot.TeleBot(TOKEN)

def log_to_topic(text, level=logging.INFO):
    """Пишет в логгер (stdout + файл) и в тему 'Логи и баны' в группе."""
    logger.log(level, _strip_html(text))
    try:
        bot.send_message(ADMIN_GROUP_ID, text, message_thread_id=BANS_TOPIC_ID, parse_mode="HTML")
    except Exception as e:
        logger.error(f"[log_to_topic] Не удалось отправить лог в топик: {e}")

class BotExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        logger.exception("Необработанная ошибка в боте")
        log_to_topic(f"🔥 <b>Необработанная ошибка в боте:</b>\n<code>{html.escape(str(exception))}</code>", level=logging.ERROR)
        return True  # не даём поллингу упасть, продолжаем работу

bot.exception_handler = BotExceptionHandler()

# --- ФУНКЦИЯ ПРОВЕРКИ ПОДПИСКИ (Postgres) ---
def get_remnawave_info(tg_id):
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            database=PG_DB,
            user=PG_USER,
            password=PG_PASS,
            connect_timeout=3
        )
        with conn.cursor() as cur:
            query = """
                SELECT 
                    u.status, 
                    u.expire_at, 
                    u.traffic_limit_bytes, 
                    ut.used_traffic_bytes
                FROM users u
                LEFT JOIN user_traffic ut ON u.t_id = ut.t_id
                WHERE u.telegram_id = %s
                LIMIT 1;
            """
            cur.execute(query, (tg_id,))
            res = cur.fetchone()
            
            if not res:
                return "❌ Не найден в базе панели RemnaWave"
            
            status = res[0] or "неизвестно"
            end_date = res[1].strftime("%d.%m.%Y %H:%M") if res[1] else "—"
            t_limit_bytes = res[2] or 0
            t_used_bytes = res[3] or 0
            
            t_limit = round(t_limit_bytes / (1024 ** 3), 2) if t_limit_bytes else 0
            t_used = round(t_used_bytes / (1024 ** 3), 2)
            
            icon = "🟢" if status == "ACTIVE" else "🔴"
            limit_str = f"{t_limit} GB" if t_limit_bytes else "Безлимит"
            
            return (f"{icon} <b>Статус:</b> {status}\n"
                    f"📅 <b>До:</b> {end_date}\n"
                    f"📊 <b>Трафик:</b> {t_used} / {limit_str}")
    except Exception as e:
        return f"⚠️ Ошибка связи с БД: {e}"
    finally:
        if 'conn' in locals(): conn.close()

# --- ЛОГИКА ЛОКАЛЬНОЙ БД (SQLite) ---
def run_query(query, params=(), fetch=False, fetchall=False):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            conn.commit()

def init_db():
    run_query("CREATE TABLE IF NOT EXISTS users (uid INTEGER PRIMARY KEY, is_banned INTEGER DEFAULT 0, ban_reason TEXT, state TEXT DEFAULT '')")
    run_query("CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, uid INTEGER, thread_id INTEGER, status TEXT DEFAULT 'open', created_at REAL, last_activity REAL, title_base TEXT)")
    # Миграция: у баз, созданных до добавления тега пользователя в топик, нет колонки title_base.
    try:
        run_query("ALTER TABLE tickets ADD COLUMN title_base TEXT")
    except sqlite3.OperationalError:
        pass  # колонка уже существует
    # Отзывы на модерации хранятся в БД, чтобы переживать рестарт контейнера.
    run_query("CREATE TABLE IF NOT EXISTS pending_reviews (uid INTEGER PRIMARY KEY, text TEXT, created_at REAL)")

init_db()

# --- ТЕГ ПОЛЬЗОВАТЕЛЯ И СТАТУС ТИКЕТА В НАЗВАНИИ ТЕМЫ ---
def build_user_tag(user):
    """Возвращает @username, если он есть, иначе ID пользователя."""
    if getattr(user, "username", None):
        return f"@{user.username}"
    return f"id{user.id}"

def build_topic_title(title_base, status="open"):
    """Собирает название темы: иконка статуса + t_id + имя + тег пользователя."""
    icon = "🟢" if status == "open" else "🔒"
    return f"{icon} {title_base}"

def close_ticket_topic(thread_id, title_base=None):
    """Закрывает тему форума и переименовывает её, чтобы статус был виден в списке тем."""
    if title_base:
        try:
            bot.edit_forum_topic(ADMIN_GROUP_ID, thread_id, name=build_topic_title(title_base, "closed"))
        except Exception as e:
            logger.warning(f"Не удалось переименовать тему {thread_id} при закрытии: {e}")
    try:
        bot.close_forum_topic(ADMIN_GROUP_ID, thread_id)
    except Exception as e:
        logger.warning(f"Не удалось закрыть тему {thread_id}: {e}")

# --- ОТЗЫВЫ НА МОДЕРАЦИИ (SQLite) ---
def save_pending_review(uid, text):
    run_query("INSERT OR REPLACE INTO pending_reviews (uid, text, created_at) VALUES (?, ?, ?)",
              (uid, text, time.time()))

def get_pending_review(uid):
    row = run_query("SELECT text FROM pending_reviews WHERE uid=?", (uid,), fetch=True)
    return row[0] if row else None

def delete_pending_review(uid):
    run_query("DELETE FROM pending_reviews WHERE uid=?", (uid,))

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🎫 Открыть новый тикет"))
    markup.add(types.KeyboardButton("⭐ Отзывы"))
    return markup

def get_reviews_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    # Форматируем ссылку на канал, если пользователь забыл указать t.me/
    channel_url = REVIEWS_CHANNEL if "t.me" in REVIEWS_CHANNEL or REVIEWS_CHANNEL.startswith("http") else f"https://t.me/{REVIEWS_CHANNEL.replace('@', '')}"
    markup.add(
        types.InlineKeyboardButton("✍️ Оставить отзыв", callback_data="review_leave"),
        types.InlineKeyboardButton("👀 Посмотреть отзывы", url=channel_url)
    )
    return markup

def get_active_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("❌ Закрыть текущий тикет"))
    return markup

def get_admin_buttons(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔒 Закрыть", callback_data=f"force_close_{user_id}"),
        types.InlineKeyboardButton("🚫 Забанить", callback_data=f"banmenu_{user_id}")
    )
    return kb

def get_banned_buttons(user_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_{user_id}")
    )
    return kb

def get_moderation_buttons(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"rev_approve_{user_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rev_decline_{user_id}")
    )
    return kb

# --- ОБРАБОТКА КОМАНД ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (message.from_user.id,), fetch=True)
    if row and row[0] == 1: return bot.send_message(message.chat.id, "❌ Доступ закрыт.")
    
    # Сбрасываем стейт при старте
    run_query("INSERT OR IGNORE INTO users (uid, state) VALUES (?, '')", (message.from_user.id,))
    run_query("UPDATE users SET state='' WHERE uid=?", (message.from_user.id,))
    
    bot.send_message(message.chat.id, f"👋 {PROJECT_NAME}. Нажмите кнопку ниже для связи или отправки отзыва.", reply_markup=get_main_menu())

# --- ОБРАБОТКА CALLBACK CALLBACK QUERY ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.from_user.id

    # 1. Запрос на написание отзыва
    if call.data == "review_leave":
        ticket = run_query("SELECT ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
        if ticket:
            bot.answer_callback_query(call.id, "У вас открыт тикет! Закройте его перед написанием отзыва.", show_alert=True)
            return
        
        run_query("INSERT OR IGNORE INTO users (uid, state) VALUES (?, '')", (uid,))
        run_query("UPDATE users SET state='waiting_review' WHERE uid=?", (uid,))
        
        bot.edit_message_text("📝 Пожалуйста, напишите ваш отзыв одним сообщением. Мы ценим честную обратную связь!", 
                              chat_id=call.message.chat.id, message_id=call.message.message_id)
        bot.answer_callback_query(call.id)

    # 2. Модерация отзывов: Одобрение
    elif call.data.startswith("rev_approve_"):
        target_uid = int(call.data.split("_")[2])
        review_text = get_pending_review(target_uid)

        if not review_text:
            bot.answer_callback_query(call.id, "Ошибка: отзыв не найден (возможно, уже обработан).", show_alert=True)
            return

        try:
            # Публикуем в канал
            channel_msg = f"⭐️ <b>Новый отзыв о {PROJECT_NAME}!</b>\n\n{html.escape(review_text)}"
            bot.send_message(REVIEWS_CHANNEL, channel_msg, parse_mode="HTML")

            # Обновляем сообщение в группе модерации
            bot.edit_message_text(f"✅ <b>Отзыв одобрен и опубликован!</b>\n\nТекст:\n<i>{html.escape(review_text)}</i>",
                                  chat_id=ADMIN_GROUP_ID, message_id=call.message.message_id, parse_mode="HTML")

            # Уведомляем автора
            bot.send_message(target_uid, "🎉 Ваш отзыв прошел модерацию и был опубликован! Спасибо!")
            delete_pending_review(target_uid)
            logger.info(f"Отзыв {target_uid} одобрен и опубликован")
        except Exception as e:
            bot.answer_callback_query(call.id, "Ошибка публикации отзыва", show_alert=True)
            log_to_topic(f"⚠️ Ошибка публикации отзыва <code>{target_uid}</code>: {html.escape(str(e))}")

    # 3. Модерация отзывов: Отклонение
    elif call.data.startswith("rev_decline_"):
        target_uid = int(call.data.split("_")[2])
        review_text = get_pending_review(target_uid) or "Текст утерян"

        bot.edit_message_text(f"❌ <b>Отзыв отклонен модератором.</b>\n\nТекст:\n<s>{html.escape(review_text)}</s>",
                              chat_id=ADMIN_GROUP_ID, message_id=call.message.message_id, parse_mode="HTML")

        bot.send_message(target_uid, "❌ К сожалению, ваш отзыв не прошел модерацию.")
        delete_pending_review(target_uid)
        logger.info(f"Отзыв {target_uid} отклонён модератором")
        bot.answer_callback_query(call.id, "Отклонено")

    # 4. Закрытие тикета админом (из оригинального кода)
    elif call.data.startswith("force_close_"):
        target_uid = int(call.data.split("_")[2])
        ticket = run_query("SELECT thread_id, ticket_id, title_base FROM tickets WHERE uid=? AND status='open'", (target_uid,), fetch=True)
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (target_uid,))
            close_ticket_topic(ticket[0], ticket[2])
            bot.send_message(target_uid, "🔒 Ваш тикет был закрыт поддержкой.", reply_markup=get_main_menu())
            bot.answer_callback_query(call.id, "Тикет закрыт")

    # 5. Бан пользователя
    elif call.data.startswith("banmenu_"):
        target_uid = int(call.data.split("_")[1])
        admin_name = call.from_user.first_name or str(call.from_user.id)

        run_query("INSERT OR IGNORE INTO users (uid, state) VALUES (?, '')", (target_uid,))
        run_query("UPDATE users SET is_banned=1, ban_reason=? WHERE uid=?", ("Забанен через поддержку", target_uid))

        # Закрываем открытый тикет, если есть
        ticket = run_query("SELECT thread_id, title_base FROM tickets WHERE uid=? AND status='open'", (target_uid,), fetch=True)
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (target_uid,))
            try:
                close_ticket_topic(ticket[0], ticket[1])
            except Exception as e:
                log_to_topic(f"⚠️ Не удалось закрыть тему при бане <code>{target_uid}</code>: {html.escape(str(e))}")

        try:
            bot.send_message(target_uid, "🚫 Вы были заблокированы в боте поддержки.")
        except Exception as e:
            log_to_topic(f"⚠️ Не удалось уведомить <code>{target_uid}</code> о бане (возможно, заблокировал бота): {html.escape(str(e))}")

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                           reply_markup=get_banned_buttons(target_uid))
        except Exception as e:
            log_to_topic(f"⚠️ Не удалось обновить карточку при бане <code>{target_uid}</code>: {html.escape(str(e))}")

        log_to_topic(f"🚫 <b>Бан:</b> админ {html.escape(admin_name)} забанил <code>{target_uid}</code>")
        bot.answer_callback_query(call.id, "Пользователь забанен", show_alert=True)

    # 6. Разбан пользователя
    elif call.data.startswith("unban_"):
        target_uid = int(call.data.split("_")[1])
        admin_name = call.from_user.first_name or str(call.from_user.id)

        run_query("UPDATE users SET is_banned=0, ban_reason=NULL WHERE uid=?", (target_uid,))

        try:
            bot.send_message(target_uid, "✅ Вы были разблокированы и снова можете пользоваться поддержкой.", reply_markup=get_main_menu())
        except Exception as e:
            log_to_topic(f"⚠️ Не удалось уведомить <code>{target_uid}</code> о разбане: {html.escape(str(e))}")

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                           reply_markup=get_admin_buttons(target_uid))
        except Exception as e:
            log_to_topic(f"⚠️ Не удалось обновить карточку при разбане <code>{target_uid}</code>: {html.escape(str(e))}")

        log_to_topic(f"✅ <b>Разбан:</b> админ {html.escape(admin_name)} разбанил <code>{target_uid}</code>")
        bot.answer_callback_query(call.id, "Пользователь разбанен", show_alert=True)

# --- ОБРАБОТКА ЛИЧНЫХ СООБЩЕНИЙ ---
@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'voice'], func=lambda m: m.chat.type == 'private')
def handle_private(message):
    uid = message.from_user.id
    
    # Проверка бана 
    row = run_query("SELECT is_banned, state FROM users WHERE uid=?", (uid,), fetch=True)
    if row and row[0] == 1: return
    
    current_state = row[1] if row else ""
    ticket = run_query("SELECT ticket_id, thread_id, title_base FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)

    # Кнопка главного меню «Отзывы»
    if message.text == "⭐ Отзывы":
        if ticket: 
            return bot.send_message(message.chat.id, "У вас открыт тикет. Закройте его, чтобы управлять отзывами.")
        return bot.send_message(message.chat.id, "Выберите действие:", reply_markup=get_reviews_menu())

    # Проверка: если пользователь находится в состоянии ожидания отзыва
    if current_state == 'waiting_review':
        if message.content_type != 'text':
            return bot.send_message(message.chat.id, "⚠️ Отзыв должен быть текстовым сообщением. Пожалуйста, напишите текст:")
        
        if message.text.startswith("/") or message.text in ["🎫 Открыть новый тикет", "⭐ Отзывы"]:
            run_query("UPDATE users SET state='' WHERE uid=?", (uid,))
            return bot.send_message(message.chat.id, "Отправка отзыва отменена.", reply_markup=get_main_menu())
        
        # Сохраняем отзыв в БД (переживёт рестарт до момента модерации)
        save_pending_review(uid, message.text)
        run_query("UPDATE users SET state='' WHERE uid=?", (uid,))
        
        try:
            # Отправляем в тему модерации отзывов
            bot.send_message(
                ADMIN_GROUP_ID,
                f"📝 <b>Новый отзыв на модерацию!</b>\n"
                f"👤 От: {html.escape(message.from_user.first_name)} (ID: <code>{uid}</code>)\n\n"
                f"💬 <b>Текст отзыва:</b>\n{html.escape(message.text)}",
                message_thread_id=REVIEWS_TOPIC_ID,
                parse_mode="HTML",
                reply_markup=get_moderation_buttons(uid)
            )
            bot.send_message(message.chat.id, "⏳ Ваш отзыв успешно отправлен на модерацию! Спасибо.", reply_markup=get_main_menu())
        except Exception as e:
            bot.send_message(message.chat.id, "⚠️ Ошибка отправки отзыва модераторам. Проверьте правильность REVIEWS_TOPIC_ID.")
            log_to_topic(f"⚠️ Ошибка отправки отзыва в тему модерации от <code>{uid}</code>: {html.escape(str(e))}")
        return

    # Стандартная логика тикетов 
    if message.text == "🎫 Открыть новый тикет":
        if ticket: return bot.send_message(message.chat.id, "У вас уже есть открытый тикет.")
        
        date_prefix = datetime.datetime.now().strftime("%d%m%y")
        count = run_query("SELECT COUNT(*) FROM tickets WHERE ticket_id LIKE ?", (f"T-{date_prefix}-%",), fetch=True)[0]
        t_id = f"T-{date_prefix}-{count + 1}"
        
        user_info = get_remnawave_info(uid)
        display_name = message.from_user.first_name or str(uid)
        user_tag = build_user_tag(message.from_user)
        title_base = f"{t_id} | {display_name} ({user_tag})"
        
        try:
            topic = bot.create_forum_topic(ADMIN_GROUP_ID, build_topic_title(title_base, "open"))
            bot.send_message(
                ADMIN_GROUP_ID, 
                f"🆕 <b>Новое обращение: {t_id}</b>\n"
                f"👤 От: {html.escape(display_name)} ({html.escape(user_tag)}, ID: <code>{uid}</code>)\n\n"
                f"💳 <b>Данные подписки:</b>\n{user_info}\n\n"
                f"ℹ️ Ответьте (Reply) на сообщение клиента — тогда ответ уйдёт ему. "
                f"Сообщение без Reply останется внутренней заметкой и клиенту не покажется.",
                message_thread_id=topic.message_thread_id, 
                parse_mode="HTML", 
                reply_markup=get_admin_buttons(uid)
            )
            run_query("INSERT INTO tickets (ticket_id, uid, thread_id, status, created_at, last_activity, title_base) VALUES (?, ?, ?, 'open', ?, ?, ?)",
                      (t_id, uid, topic.message_thread_id, time.time(), time.time(), title_base))
            bot.send_message(message.chat.id, "✅ Тикет открыт. Напишите ваш вопрос.", reply_markup=get_active_menu())
            logger.info(f"Открыт тикет {t_id} (uid={uid}, tag={user_tag})")
        except Exception as e:
            bot.send_message(message.chat.id, "⚠️ Ошибка при создании тикета. Попробуйте позже.")
            log_to_topic(f"⚠️ Ошибка создания тикета для <code>{uid}</code>: {html.escape(str(e))}")

    elif message.text == "❌ Закрыть текущий тикет":
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (uid,))
            close_ticket_topic(ticket[1], ticket[2])
            bot.send_message(message.chat.id, "🏁 Тикет закрыт.", reply_markup=get_main_menu())
            logger.info(f"Пользователь uid={uid} закрыл свой тикет")
    else:
        if not ticket: return bot.send_message(message.chat.id, "⚠️ Нажмите «Открыть новый тикет».")
        bot.copy_message(ADMIN_GROUP_ID, message.chat.id, message.message_id, message_thread_id=ticket[1])
        run_query("UPDATE tickets SET last_activity=? WHERE uid=? AND status='open'", (time.time(), uid))

# --- ОБРАБОТКА СООБЩЕНИЙ ОТ АДМИНОВ ---
@bot.message_handler(
    content_types=['text', 'photo', 'video', 'document', 'voice', 'sticker', 'audio', 'animation'],
    func=lambda m: m.chat.id == ADMIN_GROUP_ID and m.message_thread_id is not None
)
def handle_admin_reply(message):
    # Игнорируем команды и сообщения ботов — их не нужно пересылать клиенту.
    if message.from_user and message.from_user.is_bot:
        return
    if message.content_type == 'text' and message.text and message.text.startswith('/'):
        return

    ticket = run_query("SELECT uid FROM tickets WHERE thread_id=? AND status='open'", (message.message_thread_id,), fetch=True)
    if not ticket:
        return

    reply = message.reply_to_message
    is_real_reply = (
        reply is not None
        and reply.message_id != message.message_thread_id
        and (reply.from_user is None or reply.from_user.is_bot)
    )

    if is_real_reply:
        try:
            bot.copy_message(ticket[0], ADMIN_GROUP_ID, message.message_id)
            run_query("UPDATE tickets SET last_activity=? WHERE thread_id=? AND status='open'", (time.time(), message.message_thread_id))
            try:
                bot.set_message_reaction(ADMIN_GROUP_ID, message.message_id, reaction=[types.ReactionTypeEmoji(emoji="👍")])
            except Exception as e:
                logger.warning(f"Не удалось поставить реакцию-подтверждение (thread={message.message_thread_id}): {e}")
        except Exception as e:
            logger.warning(f"Не удалось переслать ответ админа в тикет (thread={message.message_thread_id}): {e}")
    else:
        try:
            bot.set_message_reaction(ADMIN_GROUP_ID, message.message_id, reaction=[types.ReactionTypeEmoji(emoji="✍")])
        except Exception as e:
            logger.warning(f"Не удалось поставить реакцию-заметку (thread={message.message_thread_id}): {e}")

# --- АВТОЗАКРЫТИЕ НЕАКТИВНЫХ ТИКЕТОВ ---
def auto_close_worker():
    """Фоновый поток: закрывает тикеты без активности дольше AUTO_CLOSE_HOURS."""
    if AUTO_CLOSE_HOURS <= 0:
        logger.info("Автозакрытие тикетов отключено (AUTO_CLOSE_HOURS<=0)")
        return

    check_interval = 600  # проверяем раз в 10 минут
    logger.info(f"Автозакрытие тикетов включено: порог {AUTO_CLOSE_HOURS} ч, проверка каждые {check_interval // 60} мин")
    while True:
        time.sleep(check_interval)
        try:
            threshold = time.time() - AUTO_CLOSE_HOURS * 3600
            stale = run_query(
                "SELECT ticket_id, uid, thread_id, title_base FROM tickets WHERE status='open' AND last_activity < ?",
                (threshold,), fetchall=True
            ) or []
            for ticket_id, uid, thread_id, title_base in stale:
                run_query("UPDATE tickets SET status='closed' WHERE ticket_id=?", (ticket_id,))
                close_ticket_topic(thread_id, title_base)
                try:
                    bot.send_message(uid, "🔒 Ваш тикет был автоматически закрыт из-за отсутствия активности.", reply_markup=get_main_menu())
                except Exception as e:
                    logger.warning(f"Не удалось уведомить uid={uid} об автозакрытии: {e}")
                logger.info(f"Автозакрыт неактивный тикет {ticket_id} (uid={uid})")
            if stale:
                log_to_topic(f"🕒 <b>Автозакрытие:</b> закрыто тикетов — {len(stale)}")
        except Exception as e:
            logger.error(f"Ошибка в потоке автозакрытия: {e}")

if __name__ == "__main__":
    threading.Thread(target=auto_close_worker, daemon=True).start()
    logger.info(f"Бот «{PROJECT_NAME}» запущен, начинаю polling")
    while True:
        try:
            bot.infinity_polling(long_polling_timeout=30, timeout=60)
        except Exception as e:
            logger.error(f"Polling упал, перезапуск через 5 с: {e}")
            time.sleep(5)
