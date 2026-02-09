# 🚀 Первый запуск на новом сервере

## 📋 Пошаговая инструкция

### Шаг 1: Проверить состояние системы

```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check
```

**Что произойдет:**
- ✅ Проверит наличие данных в БД
- ✅ Определит нужна ли загрузка Genesis
- ✅ Покажет рекомендации

### Возможные результаты:

#### Вариант А: База данных пустая

```
🔍 CHECKING SYSTEM STATE
======================================================================

Current situation:
  • Action: load_genesis
  • Recommendation: Database is empty - need to load historical data (Genesis)

⚠️  Genesis data needs to be loaded!
  1. Run: docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --historical
  2. Or manually configure dates in fetch_events_config.py:
     START_DATE = datetime(2024, 7, 6)
     END_DATE = datetime(2026, 1, 5)
     MIN_VOLUME = 100_000_000
======================================================================

👉 ДЕЙСТВИЕ: Загрузите Genesis (Шаг 2)
```

#### Вариант Б: Система отстает

```
🔍 CHECKING SYSTEM STATE
======================================================================

Current situation:
  • Action: catch_up
  • Recommendation: Behind by 2 season(s) - need to catch up

⚠️  System is behind!
  Missing periods: 2
    • season2
    • season3

  💡 Tip: Run with --catch-up flag to load missing data
======================================================================

👉 ДЕЙСТВИЕ: Догоните пропущенные данные (см. ниже)
```

#### Вариант В: Система актуальна

```
🔍 CHECKING SYSTEM STATE
======================================================================

Current situation:
  • Action: continue_current
  • Recommendation: Up to date with current season

✅ System is up to date!
  Current season: season3
  Ready for daily pipeline
======================================================================

👉 ДЕЙСТВИЕ: Просто запускайте ежедневную загрузку (Шаг 4)
```

---

## 📥 Шаг 2: Загрузить Genesis (если БД пустая)

### Вариант А: Автоматическая загрузка

```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --historical
```

**Что произойдет:**
- Загрузит исторические данные (2024-07-06 → 2026-01-05)
- Фильтр: MIN_VOLUME = 100M (только крупные события)
- Примерно: 50-100 событий
- Время: ~10-30 минут

### Вариант Б: Ручная загрузка (больше контроля)

```python
# 1. Настроить fetch_events_config.py
AUTO_SEASON = False
START_DATE = datetime(2024, 7, 6)   # Genesis start
END_DATE = datetime(2026, 1, 5)     # Genesis end
MIN_VOLUME = 100_000_000  # 100M для Genesis
CLOSED_ONLY = True
```

```bash
# 2. Запустить загрузку
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local
docker exec polystars_python python scripts/fetch/fetch_redemptions.py --upload --local
docker exec polystars_python python scripts/fetch/fetch_user_closed_positions_parallel.py --upload --local
docker exec polystars_python python scripts/fetch/fetch_trader_leaderboard_parallel.py --upload --local --from-db
```

---

## 🔄 Шаг 3: Догнать пропущенные данные (если отстали)

### Автоматическая проверка отставания

```bash
docker exec polystars_scheduler python /app/scripts/season_manager.py --missing
```

**Пример вывода:**
```
Missing data for 15 day(s):
  • Day 6: 2026-01-11
  • Day 7: 2026-01-12
  • Day 8: 2026-01-13
  ... and 12 more
```

### Загрузить пропущенные дни

**Вариант А: По одному дню (рекомендуется)**

```bash
# Для каждой пропущенной даты
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force

# Или вручную указать дату (TODO: добавить параметр --date)
```

**Вариант Б: Загрузить целый сезон вручную**

```python
# fetch_events_config.py
AUTO_SEASON = False
START_DATE = datetime(2026, 1, 6)   # Season start
END_DATE = datetime(2026, 1, 15)    # Season end
MIN_VOLUME = 5_000_000  # 5M для сезонов
```

```bash
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local
# И остальные скрипты...
```

---

## ✅ Шаг 4: Запустить ежедневную загрузку

### Автоматический режим (рекомендуется)

```bash
# Настроить AUTO_SEASON
# В fetch_events_config.py:
AUTO_SEASON = True

# Запустить
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

**Что произойдет:**
1. Автоматически проверит состояние системы
2. Если Genesis не загружен → покажет ошибку
3. Если система отстает → покажет предупреждение, но продолжит
4. Загрузит данные за текущий день
5. Отметит выполнение в БД

### Проверить что данные загрузились

```bash
# Статус сезона
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Статус загрузок
docker exec polystars_postgres psql -U postgres -d polymarket -c "
SELECT * FROM current_season_status;
"
```

---

## 🔍 Как система определяет с чего начать

### Логика определения в `season_manager.py`:

```python
def determine_starting_point():
    # 1. Проверка: БД полностью пустая?
    if not has_any_data():
        return 'load_genesis'  # ← Загрузить Genesis
    
    # 2. Проверка: Genesis загружен?
    if needs_historical_load():
        return 'load_genesis'  # ← Загрузить Genesis
    
    # 3. Проверка: Какой последний загруженный сезон?
    last_loaded = get_last_loaded_season()
    current_season = get_current_season()
    
    # 4. Сравнение
    if last_loaded == current_season:
        return 'continue_current'  # ← Продолжить текущий
    else:
        return 'catch_up'  # ← Догнать отставание
```

### Проверки выполняются автоматически:

1. **При запуске `--run`:**
   ```bash
   docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
   
   # Автоматически:
   # ├─ Проверит состояние БД
   # ├─ Если Genesis нет → покажет ошибку и остановится
   # └─ Если всё ОК → запустит загрузку
   ```

2. **При запуске `--check`:**
   ```bash
   docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check
   
   # Покажет детальную информацию:
   # ├─ Что нужно загрузить
   # ├─ Какие данные пропущены
   # └─ Рекомендации по действиям
   ```

---

## 📊 SQL проверки состояния

### Проверить наличие данных

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Есть ли события?
SELECT COUNT(*) as total_events FROM events;

-- Есть ли сезоны?
SELECT * FROM seasons ORDER BY start_date;

-- Есть ли загруженные данные?
SELECT * FROM season_data_loads ORDER BY load_date DESC LIMIT 10;

-- Последний загруженный сезон
SELECT 
    season_name,
    MAX(load_date) as last_load,
    COUNT(*) as days_loaded
FROM season_data_loads
WHERE events_loaded = TRUE
GROUP BY season_name
ORDER BY MAX(load_date) DESC;
```

### Проверить Genesis

```sql
-- Genesis загружен?
SELECT COUNT(*) 
FROM season_data_loads 
WHERE season_name = 'genesis' AND events_loaded = TRUE;
-- Если 0 → Genesis не загружен
-- Если > 0 → Genesis загружен

-- Статистика Genesis
SELECT 
    COUNT(*) as events,
    MIN(volume) as min_volume,
    MAX(volume) as max_volume,
    MIN(end_date::date) as first_closed,
    MAX(end_date::date) as last_closed
FROM events
WHERE end_date::date BETWEEN '2024-07-06' AND '2026-01-05';
```

---

## 🎯 Сценарии использования

### Сценарий 1: Свежий сервер (БД пустая)

```bash
# 1. Проверить
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check
# → Покажет: "load_genesis"

# 2. Загрузить Genesis
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --historical
# → Загрузит 2024-07-06 → 2026-01-05

# 3. Проверить снова
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check
# → Покажет: "continue_current" или "catch_up" (если отстали)

# 4. Запустить ежедневную загрузку
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

### Сценарий 2: Перенос с другого сервера (БД имеет данные)

```bash
# 1. Проверить состояние
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check

# Если система актуальна:
# 2. Просто запустить
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Если система отстала (например, сервер был выключен неделю):
# 2. Догнать пропущенное
docker exec polystars_scheduler python /app/scripts/season_manager.py --missing
# 3. Загрузить пропущенные дни (по одному)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force
```

### Сценарий 3: Разработка/тестирование (частичные данные)

```bash
# 1. Очистить БД (опционально)
docker exec polystars_postgres psql -U postgres -d polymarket -c "
TRUNCATE TABLE events CASCADE;
TRUNCATE TABLE season_data_loads CASCADE;
TRUNCATE TABLE seasons CASCADE;
"

# 2. Загрузить только Genesis или только текущий сезон
# (настроить вручную через fetch_events_config.py)

# 3. Проверить
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check
```

---

## ⚠️ Частые проблемы

### Проблема: "Database is empty" но данные есть

**Причина:** Таблицы `seasons` и `season_data_loads` не созданы

**Решение:**
```bash
docker exec -i polystars_postgres psql -U postgres -d polymarket < sql/schemas/add_season_tables.sql
```

### Проблема: "Genesis data not loaded" но события есть

**Причина:** Есть events, но нет записей в `season_data_loads`

**Решение:**
```sql
-- Создать запись о Genesis вручную
INSERT INTO seasons (season_name, start_date, end_date, season_type)
VALUES ('genesis', '2024-07-06', '2026-01-05', 'genesis')
ON CONFLICT (season_name) DO NOTHING;

INSERT INTO season_data_loads (season_name, load_date, day_in_season, events_loaded)
VALUES ('genesis', CURRENT_DATE, 0, TRUE)
ON CONFLICT (season_name, load_date) DO NOTHING;
```

### Проблема: Система постоянно говорит "catch_up"

**Причина:** Не обновляется таблица `season_data_loads`

**Решение:** Проверить что скрипты вызывают `mark_data_loaded()`:
```bash
docker logs polystars_scheduler | grep "Marked.*as loaded"
```

---

## ✅ Checklist первого запуска

- [ ] Docker контейнеры запущены: `docker-compose ps`
- [ ] БД доступна: `docker exec polystars_postgres pg_isready`
- [ ] Таблицы созданы: `\dt seasons` в psql
- [ ] Проверено состояние: `--check`
- [ ] Genesis загружен (если нужно): `--historical`
- [ ] Первая загрузка успешна: `--run`
- [ ] Данные в БД: `SELECT COUNT(*) FROM events`
- [ ] Season tracking работает: `SELECT * FROM season_data_loads`
- [ ] Cron настроен (для автоматизации): `crontab -l`

---

**Версия:** 1.0  
**Дата:** 2026-02-09  
**Ключевая команда:** `--check` (всегда начинайте с проверки!)
