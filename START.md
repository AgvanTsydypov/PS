# 🚀 Быстрый старт PolyStars

## За 2 минуты

### 1. Запустить Docker
```bash
docker-compose up -d
```

### 2. Проверить что работает
```bash
docker-compose ps
docker exec polystars_python python scripts/db/test_db_connection.py
```

### 3. Загрузить данные Polymarket
```bash
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py
```

### 4. Загрузить в БД
```bash
# Найти последний файл
dir output\

# Загрузить (укажите ваш файл)
docker exec polystars_python python scripts/db/supabase_uploader.py output/events_20260208_143022.json --local
```

### 5. Проверить результат
```bash
docker exec polystars_postgres psql -U postgres -d polymarket -c "SELECT COUNT(*) FROM events;"
```

### 6. Запустить Next.js (опционально)
```bash
npm install
npm run dev
# Откройте: http://localhost:3000
```

---

## Основные команды

```bash
# Управление Docker
docker-compose up -d        # Запустить
docker-compose down         # Остановить
docker-compose ps           # Статус
docker-compose logs -f      # Логи

# База данных
docker exec polystars_postgres psql -U postgres -d polymarket

# Python скрипты
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py
docker exec polystars_python python scripts/fetch/fetch_redemptions.py --upload --local
```

---

## Документация

- **DOCKER.md** - полная документация Docker
- **README.md** - общая документация проекта

---

## Проблемы?

### PostgreSQL не запускается
```bash
docker-compose logs postgres
docker-compose down -v
docker-compose up -d
```

### Python не может подключиться
Проверьте `.env`:
```env
LOCAL_DB_HOST=postgres  # Должно быть "postgres"!
```

---

**Готово!** 🎉
