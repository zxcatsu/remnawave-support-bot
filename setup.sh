#!/bin/bash

echo "===================================================="
echo "      RemnaWave Support Bot Installer 🎫            "
echo "===================================================="
echo ""

if ! command -v docker &> /dev/null; then
    echo "Ошибка: Docker не установлен. Установите его перед запуском."
    exit 1
fi

if [ -f .env ]; then
    echo "ВНИМАНИЕ: Файл .env уже существует!"
    read -p "Перезаписать его? (y/n): " confirm
    if [[ $confirm != [yY] ]]; then
        echo "Установка отменена. Ваш текущий .env сохранен."
        exit 0
    fi
fi

echo "--- НАСТРОЙКА БОТА ---"
read -p "Название вашего проекта (например, My VPN): " PROJECT_NAME
read -p "Токен бота (от @BotFather): " TELEGRAM_TOKEN
read -p "ID админ-группы (начинается с -100): " ADMIN_GROUP_ID
read -p "ID темы для логов/банов: " BANS_TOPIC_ID

echo ""
echo "--- НАСТРОЙКА БАЗЫ ДАННЫХ REMNAWAVE ---"
read -p "Хост БД [remnawave-db]: " PG_HOST
PG_HOST=${PG_HOST:-remnawave-db}

read -p "Имя БД [postgres]: " PG_DB
PG_DB=${PG_DB:-postgres}

read -p "Пользователь БД [postgres]: " PG_USER
PG_USER=${PG_USER:-postgres}

read -p "Пароль от БД: " PG_PASS

read -p "Название Docker-сети панели [remnawave-network]: " NET_NAME
NET_NAME=${NET_NAME:-remnawave-network}

echo ""
echo "--- НАСТРОЙКА ОТЗЫВОВ (Enter = пропустить) ---"
read -p "ID темы для модерации отзывов [1]: " REVIEWS_TOPIC_ID
REVIEWS_TOPIC_ID=${REVIEWS_TOPIC_ID:-1}

read -p "Канал для публикации отзывов [@my_reviews_channel]: " REVIEWS_CHANNEL
REVIEWS_CHANNEL=${REVIEWS_CHANNEL:-@my_reviews_channel}

echo ""
echo "--- НАСТРОЙКА МЕНЮ (/start) ---"
echo "Баннер: положите файл в assets/ (menu.png / menu.gif / menu.mp4)"
read -p "Путь к баннеру [assets/menu.png]: " MENU_MEDIA
MENU_MEDIA=${MENU_MEDIA:-assets/menu.png}

read -p "Кнопка FAQ [❓ FAQ]: " BTN_FAQ
BTN_FAQ=${BTN_FAQ:-❓ FAQ}

read -p "Кнопка тикета [🎫 Отправить тикет]: " BTN_TICKET
BTN_TICKET=${BTN_TICKET:-🎫 Отправить тикет}

read -p "Кнопка отзывов [⭐ Отзывы]: " BTN_REVIEWS
BTN_REVIEWS=${BTN_REVIEWS:-⭐ Отзывы}

echo ""
echo "Создаю .env файл..."

cat <<EOT > .env
# --- Основные настройки ---
PROJECT_NAME=$PROJECT_NAME
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ADMIN_GROUP_ID=$ADMIN_GROUP_ID
BANS_TOPIC_ID=$BANS_TOPIC_ID

# --- База данных RemnaWave ---
PG_HOST=$PG_HOST
PG_DB=$PG_DB
PG_USER=$PG_USER
PG_PASS=$PG_PASS

# --- Docker-сеть ---
EXTERNAL_NETWORK_NAME=$NET_NAME

# --- Автозакрытие тикетов (0 = выключено) ---
AUTO_CLOSE_HOURS=24

# --- Отзывы ---
REVIEWS_TOPIC_ID=$REVIEWS_TOPIC_ID
REVIEWS_CHANNEL=$REVIEWS_CHANNEL

# --- Меню (/start) ---
MENU_MEDIA=$MENU_MEDIA
FAQ_MEDIA=
REVIEWS_MEDIA=
TICKET_MEDIA=

MENU_TEXT_FILE=texts/menu.txt
FAQ_FILE=texts/faq.txt
REVIEWS_TEXT_FILE=texts/reviews.txt
TICKET_TEXT_FILE=texts/ticket.txt

BTN_FAQ=$BTN_FAQ
BTN_TICKET=$BTN_TICKET
BTN_REVIEWS=$BTN_REVIEWS

# --- Прочее ---
TZ=Europe/Moscow
EOT

echo "Файл .env успешно создан!"
echo ""
echo "Тексты разделов редактируются в папке texts/ без перезапуска бота."
echo "Поддерживаются HTML-теги и {PROJECT_NAME} как плейсхолдер."
echo ""
read -p "Запустить бота прямо сейчас? (y/n): " run_now

if [[ $run_now == [yY] ]]; then
    docker compose up -d --build
    echo "===================================================="
    echo " Бот запущен! Логи: docker compose logs -f -t"
    echo "===================================================="
else
    echo "Для запуска: docker compose up -d --build"
fi
