#!/bin/bash
# Docker entrypoint script для Python контейнера

set -e

echo "🐍 PS Python Container Starting..."

# Проверка подключения к PostgreSQL
echo "⏳ Ожидание PostgreSQL..."
until python scripts/db/test_db_connection.py 2>/dev/null; do
  echo "PostgreSQL недоступен - ждем 2 секунды..."
  sleep 2
done

echo "✅ PostgreSQL подключен!"

# Выполнить команду переданную в CMD
exec "$@"
