<div align="center">

# 🎫 RemnaWave Support Bot

**Телеграм-бот техподдержки для VPN-панелей на базе [RemnaWave](https://remna.st/)**

Тикеты · карточка клиента · отзывы · автозакрытие · баны · кастомные баннеры и тексты · premium emoji

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

</div>

---

## ✨ Возможности

| | |
|---|---|
| 🖼️ **Баннеры** | Фото / GIF / видео для каждого раздела (меню, FAQ, отзывы, тикет). Общий MENU_MEDIA — fallback для всех. |
| 📝 **Текстовые файлы** | `texts/*.txt` — HTML + premium emoji. Редактируйте без перезапуска бота. |
| 💎 **Premium Emoji** | В текстах и названиях кнопок: `<tg-emoji emoji-id="...">` |
| 🔌 **Тариф из RemnaWave** | Карточка клиента: статус, **тариф**, дата, трафик (из Postgres панели). |
| 💬 **Forums / Topics** | Каждый тикет — отдельная тема в группе с карточкой и inline-кнопками. |
| ⭐ **Отзывы с модерацией** | Отзыв → одобрить/отклонить → публикация в канал. Хранятся в SQLite. |
| ⏰ **Автозакрытие** | Неактивные тикеты закрываются через `AUTO_CLOSE_HOURS` часов. |
| 🚫 **Баны** | Один клик из карточки тикета. |

---

## 🚀 Установка

```bash
git clone https://github.com/zxcatsu/remnawave-support-bot.git
cd remnawave-support-bot
chmod +x setup.sh
./setup.sh
```

---

## ⚙️ Конфигурация (`.env`)

| Переменная | Описание |
|---|---|
| `PROJECT_NAME` | Название проекта |
| `TELEGRAM_TOKEN` | Токен от @BotFather |
| `ADMIN_GROUP_ID` | ID группы с Topics (`-100...`) |
| `BANS_TOPIC_ID` | ID темы для логов/банов |
| `PG_HOST/DB/USER/PASS` | Доступ к Postgres RemnaWave |
| `PG_TARIFF_FIELD` | Поле тарифа в таблице `users` (по умолч. `description`) |
| `AUTO_CLOSE_HOURS` | Часов до автозакрытия (`0` = выкл) |
| `REVIEWS_TOPIC_ID/CHANNEL` | Тема модерации + канал публикации |
| `MENU_MEDIA` | Универсальный баннер (`assets/menu.png/gif/mp4`) |
| `FAQ_MEDIA` / `REVIEWS_MEDIA` / `TICKET_MEDIA` | Раздельные баннеры (пусто → MENU_MEDIA) |
| `MENU_TEXT_FILE` … `TICKET_TEXT_FILE` | Пути к текстовым файлам разделов |
| `BTN_FAQ` / `BTN_TICKET` / `BTN_REVIEWS` | Названия кнопок главного меню |

---

## 🖼️ Баннеры

Положите файл в `assets/` и укажите путь в `.env`:

```env
MENU_MEDIA=assets/menu.png     # показывается везде
FAQ_MEDIA=assets/faq.gif       # только в разделе FAQ
REVIEWS_MEDIA=                 # пусто → используется MENU_MEDIA
TICKET_MEDIA=assets/ticket.mp4
```

> **Кэш:** `file_id` сохраняется в SQLite. Если заменили файл — очистите таблицу `media_cache` в `support.db`.

---

## 📝 Тексты и Premium Emoji

Все тексты живут в папке `texts/` — правьте без перезапуска бота:

```
texts/menu.txt      # главное меню
texts/faq.txt       # FAQ
texts/reviews.txt   # раздел отзывов
texts/ticket.txt    # экран открытия тикета
```

HTML-теги Telegram и premium emoji поддерживаются везде:
```html
👋 <b>Добро пожаловать!</b>
<tg-emoji emoji-id="5432289740098201671">✨</tg-emoji> VIP-поддержка
```

ID premium emoji → [@getidsbot](https://t.me/getidsbot)

---

## 💡 Сценарий работы

```
/start → [баннер + текст + кнопки]
  ├─ ❓ FAQ       → текст faq.txt (то же сообщение редактируется)
  ├─ 🎫 Тикет    → экран ticket.txt → [Открыть тикет] → тред в группе
  └─ ⭐ Отзывы   → экран reviews.txt → написать / посмотреть

В тикете: пишете в чат → бот пересылает в тред.
Админ: Reply на сообщение клиента → ответ уходит пользователю.
```

---

## 📝 Логи

```bash
docker compose logs -f -t
tail -f bot.log
```

## 🔄 Обновление

```bash
git pull && docker compose up -d --build
```

---

## 🙏 Благодарности

Основано на оригинальной разработке [**@GH-Sa1n**](https://github.com/GH-Sa1n/remnawave_bedolaga_support_bot).
Переработано и развито [**@zxcatsu**](https://github.com/zxcatsu).

<div align="center">Сделано для сообщества RemnaWave 💙</div>
