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
| Python | `polystars_python` | - | Скрипты для ETL (ручной запуск) |
| Scheduler | `polystars_scheduler` | - | Автоматическая загрузка данных (1 раз в сутки) |

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
# Ручная загрузка данных
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py
docker exec polystars_python python scripts/fetch/fetch_redemptions.py --upload --local
docker exec polystars_python python scripts/fetch/fetch_user_closed_positions_parallel.py
docker exec polystars_python python scripts/fetch/fetch_trader_leaderboard_parallel.py

# Загрузка в БД из JSON
docker exec polystars_python python scripts/db/supabase_uploader.py output/events_20260208.json --local

# Зайти в контейнер
docker exec -it polystars_python bash
```

### Scheduler (автоматическая загрузка)
```bash
# Запустить загрузку вручную
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Проверить статус сезона
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Проверить статус планировщика
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --status

# Логи scheduler
docker logs polystars_scheduler -f
docker exec polystars_scheduler tail -f /app/logs/scheduler.log

# Проверить cron
docker exec polystars_scheduler crontab -l
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

### Автоматический режим (рекомендуется)
```bash
# 1. Запустить Docker (включая scheduler)
docker-compose up -d

# 2. Проверить что работает
docker-compose ps
docker exec polystars_python python scripts/db/test_db_connection.py

# 3. Проверить статус автозагрузки
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# 4. Данные будут загружаться автоматически каждый день в 2:00 UTC
# Или запустить вручную:
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# 5. Проверить результат
docker exec polystars_postgres psql -U postgres -d polymarket -c "SELECT COUNT(*) FROM events;"

# 6. Запустить Next.js (локально)
npm run dev
```

### Ручной режим (для тестирования)
```bash
# 1. Запустить Docker без scheduler
docker-compose up -d postgres python_scripts

# 2. Проверить подключение
docker exec polystars_python python scripts/db/test_db_connection.py

# 3. Загрузить данные вручную
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local

# 4. Проверить результат
docker exec polystars_postgres psql -U postgres -d polymarket -c "SELECT COUNT(*) FROM events;"
```

---

## Автоматическая загрузка данных (Scheduler)

### Что это?
Система автоматической загрузки данных с поддержкой **сезонов**:
- **genesis** - исторические данные (до 2026-02-09)
- **season1, season2, ...** - текущие сезоны (по 10 дней каждый)

### Как работает?
Scheduler запускается **1 раз в сутки** (в 2:00 UTC) и выполняет:
1. ✅ Проверка текущего сезона и дня
2. ✅ Загрузка Events
3. ✅ Загрузка Redemptions
4. ✅ Загрузка User Closed Positions
5. ✅ Загрузка Trader Leaderboard
6. ✅ Отметка выполнения в БД

### Команды
```bash
# Проверить статус
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Запустить загрузку вручную
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Принудительная перезагрузка
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force

# Тестовый запуск (без выполнения)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --dry-run

# Логи
docker logs polystars_scheduler -f
docker exec polystars_scheduler tail -f /app/logs/scheduler.log
```

### Подробная документация
См. **[SCHEDULER_SETUP.md](./SCHEDULER_SETUP.md)** для полной документации по:
- Настройке расписания
- Работе с сезонами
- Устранению неполадок
- Мониторингу прогресса

---

**Версия:** 2.0  
**Дата:** 2026-02-09  
**Обновления:** Добавлен Scheduler для автоматической загрузки данных
