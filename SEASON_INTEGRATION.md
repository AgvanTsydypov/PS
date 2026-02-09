# 🔗 Интеграция сезонов в скрипты загрузки данных

## 🎯 Проблема

У нас 4 скрипта, которые работают последовательно:
1. **Events** → загружает события
2. **Redemptions** → использует conditionId из events
3. **Positions** → использует wallet addresses из redemptions  
4. **Leaderboard** → использует wallet addresses из redemptions

**Вопрос:** Как каждый скрипт узнает, что нужно загружать данные только за текущий сезон (например, Season 1)?

## ✅ Решение: Season Context

Создан **централизованный контекст сезона**, который:
1. Устанавливается в начале pipeline (`daily_scheduler.py`)
2. Читается каждым скриптом
3. Очищается после завершения pipeline

### Как это работает

```
┌─────────────────────────────────────────────────────────────┐
│  daily_scheduler.py (начало pipeline)                       │
│                                                              │
│  1. Определяет текущий сезон: season1                       │
│  2. Создает SeasonContext:                                  │
│     - season_name: "season1"                                │
│     - start_date: 2026-01-06                                │
│     - end_date: 2026-01-15                                  │
│     - season_type: "regular"                                │
│  3. Сохраняет в /tmp/polystars_season_context.json         │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: fetch_events_parallel_optimized.py                 │
│                                                              │
│  - Читает SeasonContext                                     │
│  - Фильтрует события по датам сезона                        │
│  - Применяет MIN_VOLUME (5M для season1)                    │
│  - Сохраняет события за 2026-01-06 → 2026-01-15            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: fetch_redemptions.py                               │
│                                                              │
│  - Читает SeasonContext                                     │
│  - Берет conditionId ТОЛЬКО из событий текущего сезона      │
│  - Фильтрует redemptions по этим conditionId                │
│  - Загружает redemptions только за Season 1                 │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 3: fetch_user_closed_positions_parallel.py            │
│                                                              │
│  - Читает SeasonContext                                     │
│  - SQL запрос фильтрует по датам сезона:                    │
│    WHERE r.timestamp_human BETWEEN '2026-01-06' AND        │
│          '2026-01-15'                                       │
│  - Загружает positions только для wallet addresses,         │
│    которые делали redemptions в Season 1                    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 4: fetch_trader_leaderboard_parallel.py               │
│                                                              │
│  - Читает SeasonContext                                     │
│  - SQL запрос фильтрует по датам сезона                     │
│  - Загружает leaderboard для тех же wallet addresses        │
│  - Данные только за Season 1                                │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  daily_scheduler.py (конец pipeline)                        │
│                                                              │
│  - Очищает SeasonContext                                    │
│  - Удаляет /tmp/polystars_season_context.json              │
└─────────────────────────────────────────────────────────────┘
```

---

## 📝 Реализация

### 1. Season Context (`scripts/season_context.py`)

```python
from scripts.season_context import SeasonContext

# Установить контекст (в scheduler)
context = SeasonContext()
context.set_current_season(date(2026, 1, 10))

# Прочитать контекст (в любом скрипте)
context = SeasonContext()
if context.has_active_season():
    season_info = context.get_season_info()
    # {
    #   'season_name': 'season1',
    #   'season_type': 'regular',
    #   'start_date': date(2026, 1, 6),
    #   'end_date': date(2026, 1, 15),
    #   'day': 5
    # }
```

### 2. Daily Scheduler (устанавливает контекст)

```python
# В scripts/daily_scheduler.py

def run_daily_pipeline(self, target_date: date = None, force: bool = False):
    # ... получение season_info ...
    
    # Установить контекст для всех скриптов
    season_context = SeasonContext()
    season_context.set_current_season(target_date, season_info)
    
    # Запустить все скрипты
    # ...
    
    # Очистить контекст после завершения
    season_context.clear()
```

### 3. Скрипты читают контекст

#### Events (уже настроен через config)

```python
# Через fetch_events_config_loader
if config.AUTO_SEASON:
    context = SeasonContext()
    season_info = context.get_season_info()
    # Применяет даты и MIN_VOLUME автоматически
```

#### Redemptions

```python
# В fetch_redemptions.py
from scripts.season_context import SeasonContext

context = SeasonContext()
if context.has_active_season():
    season_info = context.get_season_info()
    print(f"📅 Using season filter: {season_info['season_name']}")
    
    # Загружает conditionId только из событий текущего сезона
    # (события уже отфильтрованы на Step 1)
```

#### Positions

```python
# В fetch_user_closed_positions_parallel.py
from scripts.season_context import SeasonContext

context = SeasonContext()
if context.has_active_season():
    season_info = context.get_season_info()
    start_date, end_date = context.get_date_range()
    
    # SQL запрос с фильтром по датам
    sql_query = f"""
        SELECT DISTINCT r.redeemer_address, m.condition_id
        FROM redemptions r
        JOIN markets m ON r.market_id = m.id
        WHERE r.timestamp_human::date BETWEEN '{start_date}' AND '{end_date}'
    """
```

#### Leaderboard

```python
# В fetch_trader_leaderboard_parallel.py
from scripts.season_context import SeasonContext

context = SeasonContext()
if context.has_active_season():
    season_info = context.get_season_info()
    start_date, end_date = context.get_date_range()
    
    # SQL запрос с фильтром по датам
    sql_query = f"""
        SELECT DISTINCT redeemer_address
        FROM redemptions
        WHERE timestamp_human::date BETWEEN '{start_date}' AND '{end_date}'
    """
```

---

## 🔄 Каскадная фильтрация

### Ключевая идея
Данные фильтруются **каскадом** через шаги:

1. **Events (Step 1):** Фильтрует по датам сезона
   - Загружает только события, которые закрылись в текущем сезоне
   - Применяет MIN_VOLUME (5M для season, 100M для genesis)

2. **Redemptions (Step 2):** Использует conditionId из Step 1
   - Не нужна дополнительная фильтрация по датам
   - Загружает redemptions только для событий из Step 1

3. **Positions (Step 3):** Использует wallet addresses из Step 2
   - SQL фильтрует redemptions по датам сезона
   - Берет только тех пользователей, кто делал redemptions в сезоне

4. **Leaderboard (Step 4):** Использует wallet addresses из Step 2
   - SQL фильтрует redemptions по датам сезона
   - Загружает рейтинг для пользователей, активных в сезоне

### Пример для Season 1

```
Season 1: 2026-01-06 → 2026-01-15 (10 дней)

STEP 1: Events
├─ Загружено: 30 событий (закрылись в эти 10 дней)
├─ Фильтр: volume >= 5M, closed = true
└─ conditionIds: [cond1, cond2, ..., cond30]

STEP 2: Redemptions
├─ Источник: conditionIds из Step 1
├─ Загружено: 5000 redemptions для этих 30 событий
└─ Wallet addresses: [wallet1, wallet2, ..., wallet500]

STEP 3: Positions
├─ Источник: wallet addresses из Step 2
├─ Фильтр: redemptions в период 2026-01-06 → 2026-01-15
└─ Загружено: 10000 positions для этих 500 wallets

STEP 4: Leaderboard
├─ Источник: wallet addresses из Step 2
├─ Фильтр: активность в период 2026-01-06 → 2026-01-15
└─ Загружено: 500 leaderboard entries
```

---

## 🛠️ Как использовать

### Автоматический режим (рекомендуется)

```bash
# Scheduler всё сделает сам
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Что происходит:
# 1. Определяет текущий сезон
# 2. Устанавливает SeasonContext
# 3. Запускает все 4 скрипта
# 4. Каждый скрипт читает контекст и фильтрует данные
# 5. Очищает контекст после завершения
```

### Ручной режим

```bash
# 1. Установить контекст вручную
docker exec polystars_scheduler python /app/scripts/season_context.py --set 2026-01-10

# 2. Проверить контекст
docker exec polystars_scheduler python /app/scripts/season_context.py --status

# 3. Запустить скрипты по отдельности (они будут использовать контекст)
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local
docker exec polystars_python python scripts/fetch/fetch_redemptions.py --upload --local
# И т.д.

# 4. Очистить контекст
docker exec polystars_scheduler python /app/scripts/season_context.py --clear
```

---

## 📊 Проверка фильтрации

### Убедиться что данные за правильный сезон

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Проверить события за сезон
SELECT 
    COUNT(*) as total_events,
    MIN(end_date::date) as first_closed,
    MAX(end_date::date) as last_closed,
    MIN(volume) as min_volume,
    MAX(volume) as max_volume
FROM events
WHERE end_date::date BETWEEN '2026-01-06' AND '2026-01-15';

-- Проверить redemptions за сезон
SELECT 
    COUNT(*) as total_redemptions,
    COUNT(DISTINCT redeemer_address) as unique_wallets,
    MIN(timestamp_human::date) as first_redemption,
    MAX(timestamp_human::date) as last_redemption
FROM redemptions r
JOIN markets m ON r.market_id = m.id
JOIN events e ON m.event_id = e.id
WHERE e.end_date::date BETWEEN '2026-01-06' AND '2026-01-15';

-- Проверить что данные НЕ за пределами сезона
SELECT COUNT(*) FROM events
WHERE end_date::date < '2026-01-06' OR end_date::date > '2026-01-15';
-- Должно быть 0!
```

---

## ⚠️ Важные замечания

### 1. Временный файл контекста
Контекст хранится в `/tmp/polystars_season_context.json`:
- ✅ Автоматически создается при запуске pipeline
- ✅ Автоматически удаляется после завершения
- ⚠️ Если pipeline прерван (Ctrl+C) - контекст останется
- 🔧 Решение: `season_context.py --clear`

### 2. Параллельные запуски
Если запустить несколько pipeline одновременно:
- ⚠️ Они будут использовать ОДИН контекст
- ⚠️ Последний установленный контекст перезапишет предыдущий
- 🔧 Решение: Не запускайте параллельные pipeline

### 3. Ручной запуск скриптов
Если запустить скрипт вручную БЕЗ установки контекста:
- ⚠️ Скрипт НЕ будет фильтровать по сезону
- ⚠️ Загрузит ВСЕ данные (если AUTO_SEASON = False)
- 🔧 Решение: Установите контекст перед запуском

### 4. Genesis vs Seasons
- **Genesis:** Исторические данные, фильтр 100M
- **Seasons:** Текущие данные, фильтр 5M
- Контекст автоматически определяет тип и применяет правильный MIN_VOLUME

---

## 🔧 Отладка

### Проверить текущий контекст

```bash
docker exec polystars_scheduler python /app/scripts/season_context.py --status
```

### Установить контекст для тестирования

```bash
# Season 1
docker exec polystars_scheduler python /app/scripts/season_context.py --set 2026-01-10

# Genesis
docker exec polystars_scheduler python /app/scripts/season_context.py --set 2025-06-01
```

### Очистить "застрявший" контекст

```bash
docker exec polystars_scheduler python /app/scripts/season_context.py --clear
```

### Проверить файл контекста напрямую

```bash
docker exec polystars_scheduler cat /tmp/polystars_season_context.json
```

---

## ✅ Checklist интеграции

- [ ] `season_context.py` создан и работает
- [ ] `daily_scheduler.py` устанавливает контекст
- [ ] `fetch_events_config_loader.py` читает контекст (через AUTO_SEASON)
- [ ] `fetch_redemptions.py` фильтрует по conditionId из events сезона
- [ ] `fetch_user_closed_positions_parallel.py` фильтрует по датам сезона
- [ ] `fetch_trader_leaderboard_parallel.py` фильтрует по датам сезона
- [ ] Тестовый запуск прошел успешно
- [ ] Проверены данные в БД (только за нужный сезон)

---

**Версия:** 1.0  
**Дата:** 2026-02-09  
**Ключевая фича:** Централизованный Season Context для всех скриптов
