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
read -p "Токен бота (от BotFather): " TELEGRAM_TOKEN
read -p "ID админ-группы (начинается с -100): " ADMIN_GROUP_ID
read -p "ID темы для логов/банов (например, 22): " BANS_TOPIC_ID

echo ""
echo "--- НАСТРОЙКА БАЗЫ ДАННЫХ REMNAWAVE ---"
read -p "Хост БД [remnawave_bot_db]: " PG_HOST
PG_HOST=${PG_HOST:-remnawave_bot_db}

read -p "Имя БД [remnawave_bot]: " PG_DB
PG_DB=${PG_DB:-remnawave_bot}

read -p "Пользователь БД [remnawave_user]: " PG_USER
PG_USER=${PG_USER:-remnawave_user}

read -p "Пароль от БД: " PG_PASS

read -p "Название Docker-сети панели (docker network ls): " NET_NAME

echo ""
echo "--- НАСТРОЙКА ОТЗЫВОВ (можно пропустить, нажав Enter) ---"
read -p "ID темы для модерации отзывов [1]: " REVIEWS_TOPIC_ID
REVIEWS_TOPIC_ID=${REVIEWS_TOPIC_ID:-1}

read -p "Канал для публикации отзывов [@my_reviews_channel]: " REVIEWS_CHANNEL
REVIEWS_CHANNEL=${REVIEWS_CHANNEL:-@my_reviews_channel}

echo "Создаю .env файл..."

cat <<EOT > .env
PROJECT_NAME=$PROJECT_NAME
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ADMIN_GROUP_ID=$ADMIN_GROUP_ID
BANS_TOPIC_ID=$BANS_TOPIC_ID
PG_HOST=$PG_HOST
PG_DB=$PG_DB
PG_USER=$PG_USER
PG_PASS=$PG_PASS
EXTERNAL_NETWORK_NAME=$NET_NAME
AUTO_CLOSE_HOURS=24
TZ=Europe/Moscow
REVIEWS_TOPIC_ID=$REVIEWS_TOPIC_ID
REVIEWS_CHANNEL=$REVIEWS_CHANNEL
EOT

echo "Файл .env успешно создан!"
echo ""
read -p "Запустить бота прямо сейчас? (y/n): " run_now

if [[ $run_now == [yY] ]]; then
    docker compose up -d --build
    echo "===================================================="
    echo " Бот запущен! Просмотр логов: docker compose logs -f -t"
    echo "===================================================="
else
    echo "Для ручного запуска используйте команду: docker compose up -d --build"
fi
