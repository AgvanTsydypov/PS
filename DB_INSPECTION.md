# 🔍 Просмотр данных в БД и загрузка пропущенных дней

## 📊 Вариант 1: Просмотр через psql (внутри контейнера)

### Подключиться к БД:
```powershell
docker exec -it polystars_postgres psql -U postgres -d polymarket
```

### Полезные SQL запросы:

**1. Проверить количество записей в основных таблицах:**
```sql
SELECT 
    'events' as table_name, COUNT(*) as records FROM events
UNION ALL
SELECT 'markets', COUNT(*) FROM markets
UNION ALL
SELECT 'redemptions', COUNT(*) FROM redemptions
UNION ALL
SELECT 'user_closed_positions', COUNT(*) FROM user_closed_positions
UNION ALL
SELECT 'trader_leaderboard', COUNT(*) FROM trader_leaderboard;
```

**2. Посмотреть статус загрузок:**
```sql
-- Все загрузки
SELECT * FROM data_loads ORDER BY load_date DESC LIMIT 10;

-- Только Genesis
SELECT * FROM data_loads WHERE load_type = 'genesis';

-- Последние daily загрузки
SELECT * FROM data_loads WHERE load_type = 'daily' ORDER BY load_date DESC LIMIT 5;
```

**3. Проверить какие дни загружены:**
```sql
SELECT 
    load_date,
    events_loaded,
    redemptions_loaded,
    positions_loaded,
    leaderboard_loaded,
    load_type
FROM data_loads 
ORDER BY load_date DESC;
```

**4. Найти пропущенные дни:**
```sql
-- Все дни, где events не загружены
SELECT load_date FROM data_loads 
WHERE events_loaded = FALSE 
ORDER BY load_date;

-- Диапазон дат с пропусками
SELECT 
    MIN(load_date) as first_date,
    MAX(load_date) as last_date,
    COUNT(*) as total_days,
    SUM(CASE WHEN events_loaded THEN 1 ELSE 0 END) as loaded_days,
    COUNT(*) - SUM(CASE WHEN events_loaded THEN 1 ELSE 0 END) as missing_days
FROM data_loads
WHERE load_type = 'daily';
```

**5. Посмотреть примеры данных:**
```sql
-- Последние события
SELECT id, question, volume, end_date 
FROM events 
ORDER BY end_date DESC 
LIMIT 5;

-- Последние redemptions
SELECT event_id, market_id, redeemer_address, amount, timestamp 
FROM redemptions 
ORDER BY timestamp DESC 
LIMIT 5;
```

**Выход из psql:**
```sql
\q
```

---

## 📊 Вариант 2: Через Python скрипт

```powershell
docker exec polystars_scheduler python -c "
from scripts.data_loading_manager import DataLoadingManager
manager = DataLoadingManager()
manager.print_status()
"
```

---

## 🔄 Загрузка пропущенных дней

### Проверить статус (покажет пропущенные дни):
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
```

**Вывод будет примерно таким:**
```
⚠️  Missing Data:
  Last loaded: 2026-02-05
  Missing days: 3
    • 2026-02-06
    • 2026-02-07
    • 2026-02-08
  
  💡 Run with --catch-up to load missing data
```

### Загрузить пропущенные дни автоматически:
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up
```

**Что произойдет:**
1. Система найдет все пропущенные дни от конца Genesis до вчера
2. Загрузит данные для каждого пропущенного дня:
   - Events для этого дня
   - Redemptions/Positions/Leaderboard для ЭТОГО ЖЕ дня (без задержки!)
3. Отметит каждый день как загруженный в `data_loads`

**Почему нет задержки в 3 дня?**
- Catch-up загружает ИСТОРИЧЕСКИЕ данные (завершившиеся >3 дней назад)
- Данные уже финальные, нет смысла ждать
- Для свежих данных (daily pipeline) задержка в 3 дня применяется

📖 **Подробнее:** См. `DATA_LAG_LOGIC.md` для полного объяснения логики

### Dry-run (проверка без загрузки):
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up --dry-run
```

---

## 🗑️ Очистить и перезагрузить (если нужно)

### Очистить только tracking (данные останутся):
```powershell
docker exec -it polystars_postgres psql -U postgres -d polymarket -c "TRUNCATE data_loads;"
```

После этого можно заново загрузить данные:
```powershell
# Сначала Genesis
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical

# Потом пропущенные дни
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up
```

### Полная очистка ВСЕХ данных (⚠️ ОПАСНО):
```powershell
docker exec -it polystars_postgres psql -U postgres -d polymarket -c "
TRUNCATE events CASCADE;
TRUNCATE markets CASCADE;
TRUNCATE redemptions CASCADE;
TRUNCATE user_positions CASCADE;
TRUNCATE trader_leaderboard CASCADE;
TRUNCATE data_loads;
"
```

---

## 📈 Мониторинг прогресса загрузки

### В реальном времени (во время загрузки):
```powershell
# В одном окне запустить загрузку
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up

# В другом окне смотреть логи
docker logs -f polystars_scheduler
```

### После загрузки:
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
```

---

## 🎯 Типичные сценарии

### Сценарий 1: Первый запуск (чистая БД)
```powershell
# 1. Проверить статус
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
# Результат: "Genesis not loaded"

# 2. Загрузить Genesis
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical

# 3. Загрузить daily данные (если нужно)
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up
```

### Сценарий 2: Система работала, но были пропуски
```powershell
# 1. Проверить статус (покажет пропущенные дни)
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check

# 2. Загрузить пропущенное
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up
```

### Сценарий 3: Ежедневная загрузка (автоматическая)
```powershell
# Запускается автоматически через cron в 2:00 UTC
# Или вручную:
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run
```

### Сценарий 4: Проверить конкретный день вручную
```powershell
# Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

# Проверить конкретную дату
SELECT * FROM data_loads WHERE load_date = '2026-02-07';

# Посмотреть сколько events за эту дату
SELECT COUNT(*) FROM events WHERE end_date::date = '2026-02-07';
```

---

## 💡 Полезные команды

### Экспорт данных в CSV:
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "\COPY (SELECT * FROM events WHERE end_date::date >= '2026-02-01') TO '/tmp/events.csv' CSV HEADER"

# Скопировать файл из контейнера
docker cp polystars_postgres:/tmp/events.csv ./events.csv
```

### Размер таблиц:
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"
```

### Последняя активность:
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "
SELECT 
    load_date,
    events_loaded_at,
    events_count,
    redemptions_count,
    positions_count,
    leaderboard_count
FROM data_loads 
ORDER BY updated_at DESC 
LIMIT 5;
"
```

Вариант 1: Удалить данные за конкретные даты (например, 2026-02-07 и 2026-02-08)
docker exec polystars_postgres psql -U postgres -d polymarket -c "
-- Удаляем tracking
DELETE FROM data_loads WHERE load_date IN ('2026-02-07', '2026-02-08');

-- Удаляем events
DELETE FROM events WHERE end_date::date IN ('2026-02-07', '2026-02-08');

-- Удаляем redemptions  
DELETE FROM redemptions WHERE timestamp_human::date IN ('2026-02-07', '2026-02-08');

-- Удаляем user_closed_positions (по дате fetched_at или created_at)
DELETE FROM user_closed_positions WHERE created_at::date IN ('2026-02-07', '2026-02-08');

-- Удаляем trader_leaderboard (по fetched_date)
DELETE FROM trader_leaderboard WHERE fetched_date IN ('2026-02-07', '2026-02-08');

SELECT 'Deletion complete!' as status;
"




Вариант 2: Удалить ВСЕ daily данные (кроме Genesis)
docker exec polystars_postgres psql -U postgres -d polymarket -c "
-- Удаляем tracking для daily (сохраняем genesis)
DELETE FROM data_loads WHERE load_type = 'daily';

-- Удаляем events после Genesis
DELETE FROM events WHERE end_date::date > '2026-02-01';

-- Удаляем redemptions после Genesis
DELETE FROM redemptions WHERE timestamp_human::date > '2026-02-01';

-- Удаляем positions после Genesis  
DELETE FROM user_closed_positions WHERE created_at::date > '2026-02-01';

-- Удаляем leaderboard после Genesis
DELETE FROM trader_leaderboard WHERE created_at::date > '2026-02-01';

SELECT 'All daily data deleted!' as status;
"





Вариант 3: Очистить ТОЛЬКО tracking (данные останутся)
docker exec polystars_postgres psql -U postgres -d polymarket -c "
-- Удаляем только записи о загрузках (данные в таблицах останутся)
DELETE FROM data_loads WHERE load_date IN ('2026-02-07', '2026-02-08');

SELECT 'Tracking deleted!' as status;
"