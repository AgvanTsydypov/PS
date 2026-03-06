# 📊 PolyStars Database Schema

Структура базы данных для проекта PolyStars.

## 📁 Файлы в этой папке

### 🚀 **init-db.sql** - ГЛАВНЫЙ ФАЙЛ (для Docker)
Единый файл инициализации, создает ВСЕ таблицы сразу.

**Используется для:**
- ✅ Автоматическая инициализация PostgreSQL в Docker (первый запуск)
- ✅ Быстрое развертывание на новых серверах
- ✅ Полный reset базы данных

**Создает таблицы:**
1. `events` - события Polymarket
2. `markets` - рынки предсказаний
3. `fetch_metadata` - метаданные загрузок
4. `redemptions` - выплаты пользователям
5. `user_closed_positions` - закрытые позиции трейдеров
6. `trader_leaderboard` - рейтинг трейдеров
7. `data_loads` - трекинг ежедневных загрузок

**Также создает views:**
- `events_summary` - события с статистикой
- `top_volume_events` - топ по объему
- `user_pnl_summary` - PnL пользователей

---

### 📄 **Отдельные SQL файлы** (для понимания структуры)

#### `create_supabase_schema.sql`
Создает основные таблицы:
- `events` 
- `markets`
- `fetch_metadata`
- Views для аналитики

#### `SQL_REDEMPTIONS_TABLE.sql`
Создает таблицу `redemptions`:
- История выплат пользователям
- Индексы для быстрого поиска

#### `SQL_USER_CLOSED_POSITIONS_TABLE.sql`
Создает таблицу `user_closed_positions`:
- Закрытые позиции трейдеров
- PnL аналитика
- Views для топ трейдеров

#### `create_trader_leaderboard_table.sql`
Создает таблицу `trader_leaderboard`:
- Рейтинги трейдеров по категориям
- Исторические данные

---

## 🐳 Docker Integration

### Автоматическая инициализация

При **первом запуске** PostgreSQL в Docker автоматически выполняется `init-db.sql`:

```bash
docker-compose up -d
# PostgreSQL запускается → видит init-db.sql → выполняет его → создает все таблицы
```

**Важно:** Скрипт выполняется ТОЛЬКО при первом запуске (когда volume пустой).

### Как работает

В `docker-compose.yml`:
```yaml
postgres:
  volumes:
    - postgres_data:/var/lib/postgresql/data
    - ./sql/schemas/init-db.sql:/docker-entrypoint-initdb.d/01-init-db.sql:ro
```

PostgreSQL автоматически выполняет все `.sql` файлы из `/docker-entrypoint-initdb.d/` при инициализации.

---

## 🔄 Пересоздание БД

### Если нужно пересоздать все таблицы:

```bash
# 1. Остановить контейнеры и удалить volume
docker-compose down -v

# 2. Запустить снова (init-db.sql выполнится заново)
docker-compose up -d

# 3. Проверить
docker exec polystars_python python scripts/db/test_db_connection.py
```

### Или вручную запустить init-db.sql:

```bash
# В Docker
docker exec -i polystars_postgres psql -U postgres -d polymarket < sql/schemas/init-db.sql

# Локально
psql -U postgres -d polymarket -f sql/schemas/init-db.sql
```

---

## 📊 Структура таблиц

### 1. **events** - События Polymarket
```sql
id (PK), title, description, volume, liquidity, closed, end_date...
```
**Индексы:** closed, volume, end_date, active

### 2. **markets** - Рынки
```sql
id (PK), event_id (FK), question, volume_num, closed, outcomes...
```
**Индексы:** event_id, closed, volume_num

### 3. **redemptions** - Выплаты
```sql
id (PK), transaction_hash, redeemer_address, payout_usdc, timestamp_unix...
```
**Индексы:** redeemer_address, condition_id, payout_usdc

### 4. **user_closed_positions** - Позиции
```sql
id (PK), proxy_wallet, condition_id, realized_pnl, avg_price...
```
**Индексы:** proxy_wallet, realized_pnl, timestamp_unix

### 5. **trader_leaderboard** - Рейтинги
```sql
id (PK), rank, proxy_wallet, pnl, vol, category, time_period...
```
**Индексы:** proxy_wallet, category, rank, pnl, vol

### 6. **data_loads** - Трекинг загрузок
```sql
id (PK), load_date, events_loaded, redemptions_loaded, positions_loaded...
```
**Индексы:** load_date, load_type

---

## 🔍 Полезные запросы

### Проверка созданных таблиц
```sql
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;
```

### Количество записей в таблицах
```sql
SELECT 
    schemaname,
    tablename,
    n_live_tup as row_count
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
```

### Размер таблиц
```sql
SELECT 
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

### Топ трейдеров по PnL
```sql
SELECT 
    proxy_wallet,
    user_name,
    pnl,
    vol,
    rank
FROM trader_leaderboard
WHERE category = 'OVERALL' 
  AND time_period = 'WEEK'
  AND order_by = 'PNL'
ORDER BY rank
LIMIT 20;
```

---

## 🔗 Связи между таблицами

```
events (id)
   ↓ FK
markets (event_id)
   ↓ condition_id
user_closed_positions (condition_id)
redemptions (condition_id)
```

---

## 📝 Примечания

1. **Первый запуск:** init-db.sql выполняется автоматически
2. **Последующие запуски:** Схема уже создана, скрипт не выполняется
3. **Обновление схемы:** Нужно пересоздать volume или запустить migrations
4. **Prisma:** Для Next.js приложения используется `prisma/schema.prisma`

---

## 🚀 Быстрый старт

```bash
# 1. Запустить Docker
docker-compose up -d

# 2. Подождать инициализации (~10 секунд)

# 3. Проверить таблицы
docker exec -it polystars_postgres psql -U postgres -d polymarket

# 4. В psql:
\dt              # Показать все таблицы
\d events        # Описание таблицы events
SELECT COUNT(*) FROM events;
\q               # Выход
```

---

**Версия:** 1.0.0  
**Обновлено:** 2026-02-08
