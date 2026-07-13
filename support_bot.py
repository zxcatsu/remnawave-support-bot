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
import psycopg2

# ─────────────────────────────────────────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_NAME = os.getenv('PROJECT_NAME', 'VPN Support')
TOKEN         = os.getenv('TELEGRAM_TOKEN')
ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', '0'))
BANS_TOPIC_ID  = int(os.getenv('BANS_TOPIC_ID', '1'))
AUTO_CLOSE_HOURS = int(os.getenv('AUTO_CLOSE_HOURS', '24'))

REVIEWS_TOPIC_ID = int(os.getenv('REVIEWS_TOPIC_ID', '1'))
REVIEWS_CHANNEL  = os.getenv('REVIEWS_CHANNEL', '@my_reviews_channel')

PG_HOST = os.getenv('PG_HOST', 'remnawave_bot_db')
PG_DB   = os.getenv('PG_DB',   'remnawave_bot')
PG_USER = os.getenv('PG_USER', 'remnawave_user')
PG_PASS = os.getenv('PG_PASS', '')


DB_PATH  = "support.db"
LOG_PATH = os.getenv('LOG_PATH', 'bot.log')
db_lock  = threading.Lock()

# ─── Медиа-баннеры ───────────────────────────────────────────────────────────
# Для каждого раздела можно задать свой баннер.
# Если раздельный не задан — используется MENU_MEDIA (универсальный).
# Поддерживаемые форматы: .png .jpg .jpeg .webp .gif .mp4
MENU_MEDIA    = os.getenv('MENU_MEDIA',    'assets/menu.png')
FAQ_MEDIA     = os.getenv('FAQ_MEDIA',     '')   # пусто → MENU_MEDIA
REVIEWS_MEDIA = os.getenv('REVIEWS_MEDIA', '')   # пусто → MENU_MEDIA
TICKET_MEDIA  = os.getenv('TICKET_MEDIA',  '')   # пусто → MENU_MEDIA

# ─── Текстовые файлы разделов ────────────────────────────────────────────────
MENU_TEXT_FILE    = os.getenv('MENU_TEXT_FILE',    'texts/menu.txt')
FAQ_FILE          = os.getenv('FAQ_FILE',           'texts/faq.txt')
REVIEWS_TEXT_FILE = os.getenv('REVIEWS_TEXT_FILE', 'texts/reviews.txt')
TICKET_TEXT_FILE  = os.getenv('TICKET_TEXT_FILE',  'texts/ticket.txt')

# Текст меню по умолчанию (если файл не найден)
MENU_TEXT_DEFAULT = os.getenv(
    'MENU_TEXT',
    f'👋 Добро пожаловать в <b>{PROJECT_NAME}</b>!\n\nВыберите нужный раздел:'
)

# ─── Кнопки ──────────────────────────────────────────────────────────────────
BTN_FAQ     = os.getenv('BTN_FAQ',     '❓ FAQ')
BTN_TICKET  = os.getenv('BTN_TICKET',  '🎫 Отправить тикет')
BTN_REVIEWS = os.getenv('BTN_REVIEWS', '⭐ Отзывы')

# ─────────────────────────────────────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("support_bot")
logger.setLevel(logging.INFO)
_log_fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

_sh = logging.StreamHandler()
_sh.setFormatter(_log_fmt)
logger.addHandler(_sh)

_fh = RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
_fh.setFormatter(_log_fmt)
logger.addHandler(_fh)

logging.getLogger("TeleBot").setLevel(logging.WARNING)

def _strip_html(text):
    return re.sub(r"<[^>]+>", "", text)

bot = telebot.TeleBot(TOKEN)

def log_to_topic(text, level=logging.INFO):
    logger.log(level, _strip_html(text))
    try:
        bot.send_message(ADMIN_GROUP_ID, text, message_thread_id=BANS_TOPIC_ID, parse_mode="HTML")
    except Exception as e:
        logger.error(f"[log_to_topic] {e}")

class BotExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        logger.exception("Необработанная ошибка")
        log_to_topic(f"🔥 <b>Ошибка:</b>\n<code>{html.escape(str(exception))}</code>", level=logging.ERROR)
        return True

bot.exception_handler = BotExceptionHandler()

# ─────────────────────────────────────────────────────────────────────────────
# POSTGRES (данные подписки)
# ─────────────────────────────────────────────────────────────────────────────

_TRAFFIC_STRATEGY_NAMES = {
    'NO_RESET': 'Без сброса',
    'MONTH':    'Ежемесячный сброс',
    'YEAR':     'Ежегодный сброс',
    'DAY':      'Ежедневный сброс',
    'WEEK':     'Еженедельный сброс',
}

def get_remnawave_info(tg_id):
    try:
        conn = psycopg2.connect(host=PG_HOST, database=PG_DB, user=PG_USER,
                                password=PG_PASS, connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.status,
                       u.expire_at,
                       u.traffic_limit_bytes,
                       ut.used_traffic_bytes,
                       u.username,
                       u.description,
                       u.traffic_limit_strategy,
                       u.hwid_device_limit
                FROM users u
                LEFT JOIN user_traffic ut ON u.t_id = ut.t_id
                WHERE u.telegram_id = %s LIMIT 1;
            """, (tg_id,))
            res = cur.fetchone()

            if not res:
                return "❌ Не найден в базе панели RemnaWave"

            status, expire_at, t_limit_bytes, t_used_bytes, \
                username, description, strategy, hwid_limit = res

            status    = status or "неизвестно"
            end_date  = expire_at.strftime("%d.%m.%Y %H:%M") if expire_at else "—"
            t_limit_bytes = t_limit_bytes or 0
            t_used_bytes  = t_used_bytes  or 0
            t_used  = round(t_used_bytes  / (1024**3), 2)
            t_limit = round(t_limit_bytes / (1024**3), 2) if t_limit_bytes else 0
            icon      = "🟢" if status == "ACTIVE" else "🔴"
            limit_str = f"{t_limit} GB" if t_limit_bytes else "Безлимит"
            strategy_str = _TRAFFIC_STRATEGY_NAMES.get(strategy or '', strategy or '—')

            lines = [
                f"{icon} <b>Статус:</b> {html.escape(status)}",
                f"👤 <b>Логин:</b> <code>{html.escape(username or '—')}</code>",
                f"📅 <b>До:</b> {end_date}",
                f"📊 <b>Трафик:</b> {t_used} / {limit_str} ({strategy_str})",
            ]
            if hwid_limit:
                lines.append(f"📱 <b>Устройств макс.:</b> {hwid_limit}")
            if description:
                lines.append(f"📝 <b>Заметка:</b> {html.escape(description)}")

            return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Ошибка связи с БД: {e}"
    finally:
        if 'conn' in locals():
            conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# SQLITE
# ─────────────────────────────────────────────────────────────────────────────

def run_query(query, params=(), fetch=False, fetchall=False):
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            if fetch:    return cur.fetchone()
            if fetchall: return cur.fetchall()
            conn.commit()

def init_db():
    run_query("CREATE TABLE IF NOT EXISTS users (uid INTEGER PRIMARY KEY, is_banned INTEGER DEFAULT 0, ban_reason TEXT, state TEXT DEFAULT '')")
    run_query("CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, uid INTEGER, thread_id INTEGER, status TEXT DEFAULT 'open', created_at REAL, last_activity REAL, title_base TEXT)")
    try:
        run_query("ALTER TABLE tickets ADD COLUMN title_base TEXT")
    except sqlite3.OperationalError:
        pass
    run_query("CREATE TABLE IF NOT EXISTS pending_reviews (uid INTEGER PRIMARY KEY, text TEXT, created_at REAL)")
    run_query("CREATE TABLE IF NOT EXISTS media_cache (key TEXT PRIMARY KEY, file_id TEXT)")

init_db()

# ─────────────────────────────────────────────────────────────────────────────
# КЭШ МЕДИА
# ─────────────────────────────────────────────────────────────────────────────

def get_cached_file_id(key):
    row = run_query("SELECT file_id FROM media_cache WHERE key=?", (key,), fetch=True)
    return row[0] if row else None

def set_cached_file_id(key, file_id):
    run_query("INSERT OR REPLACE INTO media_cache (key, file_id) VALUES (?, ?)", (key, file_id))

# ─────────────────────────────────────────────────────────────────────────────
# ТЕКСТЫ ИЗ ФАЙЛОВ
# ─────────────────────────────────────────────────────────────────────────────

_TEXT_DEFAULTS = {
    MENU_TEXT_FILE:    MENU_TEXT_DEFAULT,
    FAQ_FILE:          "❓ <b>FAQ</b>\n\nФайл <code>texts/faq.txt</code> не найден.",
    REVIEWS_TEXT_FILE: "⭐ <b>Отзывы</b>\n\nОставьте отзыв или посмотрите отзывы:",
    TICKET_TEXT_FILE:  "🎫 <b>Открыть тикет</b>\n\nОпишите вашу проблему — мы ответим как можно скорее.",
}

def load_text(filepath):
    """Читает текст из файла. Если файл не найден — возвращает дефолт."""
    try:
        with open(filepath, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return _TEXT_DEFAULTS.get(filepath, "")

# ─────────────────────────────────────────────────────────────────────────────
# МЕДИА: определение типа и отправка
# ─────────────────────────────────────────────────────────────────────────────

def _media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.mp4', '.mov'):   return 'video'
    if ext == '.gif':             return 'animation'
    if ext in ('.png', '.jpg', '.jpeg', '.webp'): return 'photo'
    return None

def _resolve_media(section_path):
    """Возвращает путь к медиа: раздельный → универсальный → None."""
    if section_path and _media_type(section_path) and os.path.exists(section_path):
        return section_path
    if MENU_MEDIA and _media_type(MENU_MEDIA) and os.path.exists(MENU_MEDIA):
        return MENU_MEDIA
    return None

def _extract_file_id(msg, mtype):
    try:
        if mtype == 'photo':     return msg.photo[-1].file_id
        if mtype == 'video':     return msg.video.file_id
        if mtype == 'animation': return msg.animation.file_id
    except Exception:
        return None

def _send_media(chat_id, media_path, caption, keyboard):
    """Отправляет медиа с подписью. Кэширует file_id. Возвращает True при успехе."""
    mtype = _media_type(media_path)
    if not mtype:
        return False

    cache_key  = f"media:{media_path}"
    file_id    = get_cached_file_id(cache_key)
    send_kw    = dict(caption=caption, parse_mode="HTML", reply_markup=keyboard)

    try:
        if file_id:
            fns = {'photo': bot.send_photo, 'video': bot.send_video, 'animation': bot.send_animation}
            fns[mtype](chat_id, file_id, **send_kw)
        else:
            fns = {'photo': bot.send_photo, 'video': bot.send_video, 'animation': bot.send_animation}
            with open(media_path, 'rb') as f:
                sent = fns[mtype](chat_id, f, **send_kw)
            fid = _extract_file_id(sent, mtype)
            if fid:
                set_cached_file_id(cache_key, fid)
        return True
    except Exception as e:
        logger.warning(f"Ошибка отправки медиа '{media_path}': {e}")
        return False

def send_section(chat_id, text, keyboard, section_media=''):
    """
    Универсальная функция отправки раздела:
    медиа (раздельный → универсальный) + текст + кнопки.
    Если медиа нет — просто текстовое сообщение.
    """
    path = _resolve_media(section_media)
    if path and _send_media(chat_id, path, text, keyboard):
        return
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)

# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНОЕ
# ─────────────────────────────────────────────────────────────────────────────

def build_user_tag(user):
    return f"@{user.username}" if getattr(user, "username", None) else f"id{user.id}"

def build_topic_title(title_base, status="open"):
    return f"{'🟢' if status == 'open' else '🔒'} {title_base}"

def close_ticket_topic(thread_id, title_base=None):
    if title_base:
        try: bot.edit_forum_topic(ADMIN_GROUP_ID, thread_id, name=build_topic_title(title_base, "closed"))
        except Exception as e: logger.warning(f"Rename topic {thread_id}: {e}")
    try: bot.close_forum_topic(ADMIN_GROUP_ID, thread_id)
    except Exception as e: logger.warning(f"Close topic {thread_id}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# ОТЗЫВЫ (SQLite)
# ─────────────────────────────────────────────────────────────────────────────

def save_pending_review(uid, text):
    run_query("INSERT OR REPLACE INTO pending_reviews (uid, text, created_at) VALUES (?, ?, ?)",
              (uid, text, time.time()))

def get_pending_review(uid):
    row = run_query("SELECT text FROM pending_reviews WHERE uid=?", (uid,), fetch=True)
    return row[0] if row else None

def delete_pending_review(uid):
    run_query("DELETE FROM pending_reviews WHERE uid=?", (uid,))

# ─────────────────────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ─────────────────────────────────────────────────────────────────────────────

def kb_main():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(BTN_FAQ,     callback_data="menu_faq"),
        types.InlineKeyboardButton(BTN_TICKET,  callback_data="menu_ticket"),
        types.InlineKeyboardButton(BTN_REVIEWS, callback_data="menu_reviews"),
    )
    return kb

def kb_back():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back"))
    return kb

def kb_reviews():
    ch = (REVIEWS_CHANNEL if "t.me" in REVIEWS_CHANNEL or REVIEWS_CHANNEL.startswith("http")
          else f"https://t.me/{REVIEWS_CHANNEL.replace('@','')}")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✍️ Оставить отзыв",   callback_data="review_leave"),
        types.InlineKeyboardButton("👀 Посмотреть отзывы", url=ch),
    )
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back"))
    return kb

def kb_ticket_active():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ Закрыть тикет", callback_data="close_my_ticket"))
    return kb

def kb_admin(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔒 Закрыть",  callback_data=f"force_close_{uid}"),
        types.InlineKeyboardButton("🚫 Забанить", callback_data=f"banmenu_{uid}"),
    )
    return kb

def kb_banned(uid):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_{uid}"))
    return kb

def kb_moderation(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Одобрить",  callback_data=f"rev_approve_{uid}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rev_decline_{uid}"),
    )
    return kb

# ─────────────────────────────────────────────────────────────────────────────
# ХЕЛПЕРЫ: редактирование сообщения или отправка нового
# ─────────────────────────────────────────────────────────────────────────────

def _edit_or_resend(call, text, keyboard, section_media=''):
    """
    Пытается отредактировать подпись (медиа) или текст существующего сообщения.
    Если не вышло — удаляет старое и отправляет новое.
    """
    cid  = call.message.chat.id
    mid  = call.message.message_id

    # Пробуем отредактировать подпись (если сообщение с медиа)
    try:
        bot.edit_message_caption(caption=text, chat_id=cid, message_id=mid,
                                 parse_mode="HTML", reply_markup=keyboard)
        return
    except Exception:
        pass

    # Пробуем отредактировать текст (если текстовое сообщение)
    try:
        bot.edit_message_text(text, chat_id=cid, message_id=mid,
                              parse_mode="HTML", reply_markup=keyboard)
        return
    except Exception:
        pass

    # Fallback: удаляем старое и шлём новое
    try:
        bot.delete_message(cid, mid)
    except Exception:
        pass
    send_section(cid, text, keyboard, section_media)

# ─────────────────────────────────────────────────────────────────────────────
# КОМАНДА /start
# ─────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def handle_start(message):
    uid = message.from_user.id
    row = run_query("SELECT is_banned FROM users WHERE uid=?", (uid,), fetch=True)
    if row and row[0] == 1:
        return bot.send_message(message.chat.id, "❌ Доступ закрыт.")

    run_query("INSERT OR IGNORE INTO users (uid, state) VALUES (?, '')", (uid,))
    run_query("UPDATE users SET state='' WHERE uid=?", (uid,))

    send_section(message.chat.id, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)

# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK-ОБРАБОТЧИК
# ─────────────────────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid  = call.from_user.id
    data = call.data

    # ── Главное меню ─────────────────────────────────────────────────────────

    if data == "menu_back":
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        send_section(call.message.chat.id, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)
        bot.answer_callback_query(call.id)
        return

    if data == "menu_faq":
        _edit_or_resend(call, load_text(FAQ_FILE), kb_back(), FAQ_MEDIA)
        bot.answer_callback_query(call.id)
        return

    if data == "menu_reviews":
        ticket = run_query("SELECT ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
        if ticket:
            bot.answer_callback_query(call.id, "У вас открыт тикет! Закройте его перед отзывами.", show_alert=True)
            return
        _edit_or_resend(call, load_text(REVIEWS_TEXT_FILE), kb_reviews(), REVIEWS_MEDIA)
        bot.answer_callback_query(call.id)
        return

    if data == "menu_ticket":
        ticket = run_query("SELECT ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
        if ticket:
            bot.answer_callback_query(call.id, "У вас уже есть открытый тикет! Просто напишите в чат.", show_alert=True)
            return
        # Показываем экран тикета с кнопкой "Открыть"
        _edit_or_resend(call, load_text(TICKET_TEXT_FILE),
                        _kb_open_ticket(), TICKET_MEDIA)
        bot.answer_callback_query(call.id)
        return

    if data == "do_open_ticket":
        ticket = run_query("SELECT ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
        if ticket:
            bot.answer_callback_query(call.id, "Тикет уже открыт!", show_alert=True)
            return
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        _open_ticket(call.message.chat.id, call.from_user)
        bot.answer_callback_query(call.id)
        return

    if data == "close_my_ticket":
        ticket = run_query("SELECT ticket_id, thread_id, title_base FROM tickets WHERE uid=? AND status='open'",
                           (uid,), fetch=True)
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (uid,))
            close_ticket_topic(ticket[1], ticket[2])
            logger.info(f"uid={uid} закрыл тикет {ticket[0]}")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            send_section(call.message.chat.id, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)
        bot.answer_callback_query(call.id, "Тикет закрыт.")
        return

    # ── Отзыв: написать ──────────────────────────────────────────────────────

    if data == "review_leave":
        ticket = run_query("SELECT ticket_id FROM tickets WHERE uid=? AND status='open'", (uid,), fetch=True)
        if ticket:
            bot.answer_callback_query(call.id, "Закройте тикет перед написанием отзыва.", show_alert=True)
            return
        run_query("INSERT OR IGNORE INTO users (uid, state) VALUES (?, '')", (uid,))
        run_query("UPDATE users SET state='waiting_review' WHERE uid=?", (uid,))
        prompt = "📝 Напишите ваш отзыв одним сообщением:"
        try:
            bot.edit_message_caption(caption=prompt, chat_id=call.message.chat.id,
                                     message_id=call.message.message_id, parse_mode="HTML")
        except Exception:
            try:
                bot.edit_message_text(prompt, chat_id=call.message.chat.id,
                                      message_id=call.message.message_id)
            except Exception:
                bot.send_message(call.message.chat.id, prompt)
        bot.answer_callback_query(call.id)
        return

    # ── Модерация отзывов ────────────────────────────────────────────────────

    if data.startswith("rev_approve_"):
        target_uid  = int(data.split("_")[2])
        review_text = get_pending_review(target_uid)
        if not review_text:
            bot.answer_callback_query(call.id, "Отзыв не найден (уже обработан).", show_alert=True)
            return
        try:
            bot.send_message(REVIEWS_CHANNEL,
                             f"⭐️ <b>Новый отзыв о {PROJECT_NAME}!</b>\n\n{html.escape(review_text)}",
                             parse_mode="HTML")
            bot.edit_message_text(
                f"✅ <b>Одобрен и опубликован!</b>\n\n<i>{html.escape(review_text)}</i>",
                chat_id=ADMIN_GROUP_ID, message_id=call.message.message_id, parse_mode="HTML")
            bot.send_message(target_uid, "🎉 Ваш отзыв опубликован! Спасибо.")
            delete_pending_review(target_uid)
            logger.info(f"Отзыв {target_uid} одобрен")
        except Exception as e:
            bot.answer_callback_query(call.id, "Ошибка публикации", show_alert=True)
            log_to_topic(f"⚠️ Ошибка публикации отзыва <code>{target_uid}</code>: {html.escape(str(e))}")
        return

    if data.startswith("rev_decline_"):
        target_uid  = int(data.split("_")[2])
        review_text = get_pending_review(target_uid) or "Текст утерян"
        bot.edit_message_text(
            f"❌ <b>Отклонён.</b>\n\n<s>{html.escape(review_text)}</s>",
            chat_id=ADMIN_GROUP_ID, message_id=call.message.message_id, parse_mode="HTML")
        bot.send_message(target_uid, "❌ Ваш отзыв не прошёл модерацию.")
        delete_pending_review(target_uid)
        logger.info(f"Отзыв {target_uid} отклонён")
        bot.answer_callback_query(call.id, "Отклонено")
        return

    # ── Управление тикетами (из админ-группы) ───────────────────────────────

    if data.startswith("force_close_"):
        target_uid = int(data.split("_")[2])
        ticket = run_query("SELECT thread_id, ticket_id, title_base FROM tickets WHERE uid=? AND status='open'",
                           (target_uid,), fetch=True)
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (target_uid,))
            close_ticket_topic(ticket[0], ticket[2])
            try:
                bot.send_message(target_uid, "🔒 Ваш тикет закрыт поддержкой.")
                send_section(target_uid, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)
            except Exception: pass
        bot.answer_callback_query(call.id, "Тикет закрыт")
        return

    if data.startswith("banmenu_"):
        target_uid = int(data.split("_")[1])
        admin_name = call.from_user.first_name or str(call.from_user.id)
        run_query("INSERT OR IGNORE INTO users (uid, state) VALUES (?, '')", (target_uid,))
        run_query("UPDATE users SET is_banned=1, ban_reason=? WHERE uid=?", ("Забанен через поддержку", target_uid))
        ticket = run_query("SELECT thread_id, title_base FROM tickets WHERE uid=? AND status='open'",
                           (target_uid,), fetch=True)
        if ticket:
            run_query("UPDATE tickets SET status='closed' WHERE uid=? AND status='open'", (target_uid,))
            try: close_ticket_topic(ticket[0], ticket[1])
            except Exception as e:
                log_to_topic(f"⚠️ Ошибка закрытия темы при бане <code>{target_uid}</code>: {html.escape(str(e))}")
        try: bot.send_message(target_uid, "🚫 Вы заблокированы в боте поддержки.")
        except Exception as e:
            log_to_topic(f"⚠️ Не удалось уведомить <code>{target_uid}</code> о бане: {html.escape(str(e))}")
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                          reply_markup=kb_banned(target_uid))
        except Exception: pass
        log_to_topic(f"🚫 <b>Бан:</b> {html.escape(admin_name)} забанил <code>{target_uid}</code>")
        bot.answer_callback_query(call.id, "Пользователь забанен", show_alert=True)
        return

    if data.startswith("unban_"):
        target_uid = int(data.split("_")[1])
        admin_name = call.from_user.first_name or str(call.from_user.id)
        run_query("UPDATE users SET is_banned=0, ban_reason=NULL WHERE uid=?", (target_uid,))
        try:
            bot.send_message(target_uid, "✅ Вы разблокированы.")
            send_section(target_uid, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)
        except Exception as e:
            log_to_topic(f"⚠️ Не удалось уведомить <code>{target_uid}</code> о разбане: {html.escape(str(e))}")
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                          reply_markup=kb_admin(target_uid))
        except Exception: pass
        log_to_topic(f"✅ <b>Разбан:</b> {html.escape(admin_name)} разбанил <code>{target_uid}</code>")
        bot.answer_callback_query(call.id, "Пользователь разбанен", show_alert=True)
        return

# ─────────────────────────────────────────────────────────────────────────────
# ОТКРЫТИЕ ТИКЕТА
# ─────────────────────────────────────────────────────────────────────────────

def _kb_open_ticket():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("✅ Открыть тикет", callback_data="do_open_ticket"),
        types.InlineKeyboardButton("⬅️ Назад",         callback_data="menu_back"),
    )
    return kb

def _open_ticket(chat_id, tg_user):
    uid = tg_user.id
    date_prefix = datetime.datetime.now().strftime("%d%m%y")
    count = run_query("SELECT COUNT(*) FROM tickets WHERE ticket_id LIKE ?",
                      (f"T-{date_prefix}-%",), fetch=True)[0]
    t_id = f"T-{date_prefix}-{count + 1}"

    user_info    = get_remnawave_info(uid)
    display_name = tg_user.first_name or str(uid)
    user_tag     = build_user_tag(tg_user)
    title_base   = f"{t_id} | {display_name} ({user_tag})"

    try:
        topic = bot.create_forum_topic(ADMIN_GROUP_ID, build_topic_title(title_base, "open"))
        bot.send_message(
            ADMIN_GROUP_ID,
            f"🆕 <b>Новое обращение: {t_id}</b>\n"
            f"👤 От: {html.escape(display_name)} ({html.escape(user_tag)}, ID: <code>{uid}</code>)\n\n"
            f"💳 <b>Подписка:</b>\n{user_info}\n\n"
            f"ℹ️ Reply на любое сообщение бота в этом треде → ответ уйдёт клиенту. Без Reply — внутренняя заметка.",
            message_thread_id=topic.message_thread_id,
            parse_mode="HTML",
            reply_markup=kb_admin(uid),
        )
        # Отдельное сообщение-якорь — чтобы поддержка могла написать первой,
        # сделав Reply на него ещё до того как клиент что-либо напишет.
        bot.send_message(
            ADMIN_GROUP_ID,
            f"💬 <i>Ожидаем сообщения от клиента. Чтобы написать первым — сделайте Reply на это сообщение.</i>",
            message_thread_id=topic.message_thread_id,
            parse_mode="HTML",
        )
        run_query(
            "INSERT INTO tickets (ticket_id, uid, thread_id, status, created_at, last_activity, title_base) "
            "VALUES (?, ?, ?, 'open', ?, ?, ?)",
            (t_id, uid, topic.message_thread_id, time.time(), time.time(), title_base),
        )
        bot.send_message(chat_id, "✅ Тикет открыт! Напишите ваш вопрос:",
                         reply_markup=kb_ticket_active())
        logger.info(f"Открыт тикет {t_id} (uid={uid}, tag={user_tag})")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при создании тикета. Попробуйте позже.")
        log_to_topic(f"⚠️ Ошибка создания тикета <code>{uid}</code>: {html.escape(str(e))}")

# ─────────────────────────────────────────────────────────────────────────────
# ЛИЧНЫЕ СООБЩЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────

@bot.message_handler(
    content_types=['text', 'photo', 'video', 'document', 'voice'],
    func=lambda m: m.chat.type == 'private'
)
def handle_private(message):
    uid = message.from_user.id

    row = run_query("SELECT is_banned, state FROM users WHERE uid=?", (uid,), fetch=True)
    if row and row[0] == 1:
        return

    current_state = row[1] if row else ""
    ticket = run_query("SELECT ticket_id, thread_id, title_base FROM tickets WHERE uid=? AND status='open'",
                       (uid,), fetch=True)

    # Состояние: ожидаем текст отзыва
    if current_state == 'waiting_review':
        if message.content_type != 'text':
            return bot.send_message(message.chat.id, "⚠️ Отзыв должен быть текстом. Напишите:")
        if message.text.startswith("/"):
            run_query("UPDATE users SET state='' WHERE uid=?", (uid,))
            return send_section(message.chat.id, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)

        save_pending_review(uid, message.text)
        run_query("UPDATE users SET state='' WHERE uid=?", (uid,))
        try:
            bot.send_message(
                ADMIN_GROUP_ID,
                f"📝 <b>Новый отзыв на модерацию!</b>\n"
                f"👤 {html.escape(message.from_user.first_name)} (ID: <code>{uid}</code>)\n\n"
                f"💬 {html.escape(message.text)}",
                message_thread_id=REVIEWS_TOPIC_ID,
                parse_mode="HTML",
                reply_markup=kb_moderation(uid),
            )
            bot.send_message(message.chat.id, "⏳ Отзыв отправлен на модерацию! Спасибо.")
        except Exception as e:
            bot.send_message(message.chat.id, "⚠️ Ошибка отправки. Проверьте REVIEWS_TOPIC_ID.")
            log_to_topic(f"⚠️ Ошибка отзыва от <code>{uid}</code>: {html.escape(str(e))}")
        return

    # Есть открытый тикет — пересылаем сообщение в тред
    if ticket:
        bot.copy_message(ADMIN_GROUP_ID, message.chat.id, message.message_id, message_thread_id=ticket[1])
        run_query("UPDATE tickets SET last_activity=? WHERE uid=? AND status='open'", (time.time(), uid))
    else:
        # Нет ни тикета, ни состояния — показываем меню
        send_section(message.chat.id, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)

# ─────────────────────────────────────────────────────────────────────────────
# ОТВЕТЫ АДМИНОВ
# ─────────────────────────────────────────────────────────────────────────────

@bot.message_handler(
    content_types=['text', 'photo', 'video', 'document', 'voice', 'sticker', 'audio', 'animation'],
    func=lambda m: m.chat.id == ADMIN_GROUP_ID and m.message_thread_id is not None
)
def handle_admin_reply(message):
    if message.from_user and message.from_user.is_bot: return
    if message.content_type == 'text' and message.text and message.text.startswith('/'): return

    ticket = run_query("SELECT uid FROM tickets WHERE thread_id=? AND status='open'",
                       (message.message_thread_id,), fetch=True)
    if not ticket: return

    reply = message.reply_to_message
    is_reply = (reply is not None
                and reply.message_id != message.message_thread_id
                and (reply.from_user is None or reply.from_user.is_bot))

    if is_reply:
        try:
            bot.copy_message(ticket[0], ADMIN_GROUP_ID, message.message_id)
            run_query("UPDATE tickets SET last_activity=? WHERE thread_id=? AND status='open'",
                      (time.time(), message.message_thread_id))
            try:
                bot.set_message_reaction(ADMIN_GROUP_ID, message.message_id,
                                         reaction=[types.ReactionTypeEmoji(emoji="👍")])
            except Exception: pass
        except Exception as e:
            logger.warning(f"Ошибка пересылки ответа (thread={message.message_thread_id}): {e}")
    else:
        try:
            bot.set_message_reaction(ADMIN_GROUP_ID, message.message_id,
                                     reaction=[types.ReactionTypeEmoji(emoji="✍")])
        except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# АВТОЗАКРЫТИЕ ТИКЕТОВ
# ─────────────────────────────────────────────────────────────────────────────

def auto_close_worker():
    if AUTO_CLOSE_HOURS <= 0:
        logger.info("Автозакрытие отключено")
        return
    logger.info(f"Автозакрытие: порог {AUTO_CLOSE_HOURS} ч, проверка каждые 10 мин")
    while True:
        time.sleep(600)
        try:
            threshold = time.time() - AUTO_CLOSE_HOURS * 3600
            stale = run_query(
                "SELECT ticket_id, uid, thread_id, title_base FROM tickets WHERE status='open' AND last_activity < ?",
                (threshold,), fetchall=True) or []
            for ticket_id, uid, thread_id, title_base in stale:
                run_query("UPDATE tickets SET status='closed' WHERE ticket_id=?", (ticket_id,))
                close_ticket_topic(thread_id, title_base)
                try:
                    bot.send_message(uid, "🔒 Тикет закрыт из-за отсутствия активности.")
                    send_section(uid, load_text(MENU_TEXT_FILE), kb_main(), MENU_MEDIA)
                except Exception as e:
                    logger.warning(f"Не удалось уведомить uid={uid} об автозакрытии: {e}")
                logger.info(f"Автозакрыт тикет {ticket_id} (uid={uid})")
            if stale:
                log_to_topic(f"🕒 <b>Автозакрытие:</b> {len(stale)} тикет(ов)")
        except Exception as e:
            logger.error(f"Ошибка автозакрытия: {e}")


if __name__ == "__main__":
    threading.Thread(target=auto_close_worker, daemon=True).start()
    logger.info(f"Бот «{PROJECT_NAME}» запущен")
    while True:
        try:
            bot.infinity_polling(long_polling_timeout=30, timeout=60)
        except Exception as e:
            logger.error(f"Polling упал, перезапуск через 5 с: {e}")
            time.sleep(5)
