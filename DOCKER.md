# 🐳 Docker для PolyStars

## Быстрый старт

### 1. Запустить
```bash
docker-compose up -d
```

### 2. Проверить
```bash
docker-compose ps
docker exec polystars_python python scripts/db/test_db_connection.py
```

### 3. Загрузить данные
```bash
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py
docker exec polystars_python python scripts/db/supabase_uploader.py output/events_*.json --local
```

---

## Что запускается

| Сервис | Контейнер | Порт | Описание |
|--------|-----------|------|----------|
| PostgreSQL | `polystars_postgres` | 5432 | База данных |
| Python | `polystars_python` | - | Скрипты для ETL |

---

## Основные команды

### Управление
```bash
docker-compose up -d          # Запустить
docker-compose down           # Остановить
docker-compose restart        # Перезапустить
docker-compose ps             # Статус
docker-compose logs -f        # Логи
```

### База данных
```bash
# Подключиться к PostgreSQL
docker exec -it polystars_postgres psql -U postgres -d polymarket

# В psql:
\dt                           # Показать таблицы
SELECT COUNT(*) FROM events;  # Подсчет записей
\q                            # Выход

# Backup
docker exec polystars_postgres pg_dump -U postgres polymarket > backup.sql

# Restore
cat backup.sql | docker exec -i polystars_postgres psql -U postgres polymarket
```

### Python скрипты
```bash
# Загрузка данных
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py
docker exec polystars_python python scripts/fetch/fetch_redemptions.py --upload --local
docker exec polystars_python python scripts/fetch/fetch_user_closed_positions_parallel.py
docker exec polystars_python python scripts/fetch/fetch_trader_leaderboard_parallel.py

# Загрузка в БД из JSON
docker exec polystars_python python scripts/db/supabase_uploader.py output/events_20260208.json --local

# Аналитика
docker exec polystars_python python scripts/analytics/count_markets.py

# Зайти в контейнер
docker exec -it polystars_python bash
```

---

## Переменные окружения (.env)

```env
# PostgreSQL
LOCAL_DB_HOST=postgres
LOCAL_DB_PORT=5432
LOCAL_DB_NAME=polymarket
LOCAL_DB_USER=postgres
LOCAL_DB_PASSWORD=1234

# Supabase (опционально)
SUPABASE_URL=your_url
SUPABASE_KEY=your_key

# API ключи
ALCHEMY_API_KEY=your_key
```

⚠️ **Важно:** Для Docker в `LOCAL_DB_HOST` должно быть `postgres`, НЕ `localhost`!

---

## Структура

```
docker-compose.yml          # Конфигурация Docker
Dockerfile.python           # Python образ
.env                        # Переменные окружения
sql/schemas/init-db.sql     # Инициализация БД (автоматически)
scripts/                    # Python ETL скрипты
```

---

## База данных

### Таблицы
- `events` - события Polymarket
- `markets` - рынки предсказаний
- `redemptions` - выплаты пользователям
- `user_closed_positions` - закрытые позиции
- `trader_leaderboard` - рейтинги трейдеров
- `nft_claims` - заявки на NFT
- `rate_limits` - rate limiting
- `fetch_metadata` - метаданные загрузок

### Автоинициализация
При первом запуске PostgreSQL автоматически выполняет `sql/schemas/init-db.sql` и создает все таблицы.

---

## Подключение к БД

### Из Docker контейнера (Python скрипты)
```
HOST: postgres
PORT: 5432
DATABASE: polymarket
USER: postgres
PASSWORD: (из .env)
```

### С хоста (Next.js, pgAdmin)
```
HOST: localhost
PORT: 5432
DATABASE: polymarket
USER: postgres
PASSWORD: (из .env)
```

---

## Troubleshooting

### PostgreSQL не запускается
```bash
docker-compose logs postgres
docker-compose down -v
docker-compose up -d
```

### Python не может подключиться к БД
Проверьте `.env`:
```env
LOCAL_DB_HOST=postgres  # Должно быть "postgres"!
```

### Пересоздать контейнеры
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Удалить всё (включая данные БД)
```bash
docker-compose down -v  # ⚠️ Удалит все данные!
```

---

## Next.js подключение

В `.env.local`:
```env
DATABASE_URL="postgresql://postgres:1234@localhost:5432/polymarket"
```

Затем:
```bash
npm run dev
```

---

## Volumes

- `postgres_data` - данные PostgreSQL (persistent)
- `./scripts` → `/app/scripts` (read-only)
- `./output` → `/app/output` (read-write)
- `./data` → `/app/data` (read-write)
- `./logs` → `/app/logs` (read-write)

---

## Типичный workflow

```bash
# 1. Запустить Docker
docker-compose up -d

# 2. Проверить что работает
docker-compose ps
docker exec polystars_python python scripts/db/test_db_connection.py

# 3. Загрузить данные
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py

# 4. Загрузить в БД
docker exec polystars_python python scripts/db/supabase_uploader.py output/events_*.json --local

# 5. Проверить результат
docker exec polystars_postgres psql -U postgres -d polymarket -c "SELECT COUNT(*) FROM events;"

# 6. Запустить Next.js (локально)
npm run dev
```

---

**Версия:** 1.0  
**Дата:** 2026-02-08
