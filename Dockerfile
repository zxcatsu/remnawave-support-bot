FROM python:3.10-slim
WORKDIR /app
# Установка системных зависимостей для Postgres драйвера
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY support_bot.py .
CMD ["python", "support_bot.py"]
