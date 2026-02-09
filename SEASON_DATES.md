# 📅 Структура сезонов и дат

## 🕰️ Периоды загрузки данных

### Genesis (Исторические данные)
```
Период:    2024-07-06  →  2026-01-05
Тип:       Исторические данные
Описание:  Все данные до запуска системы сезонов
Длина:     ~18 месяцев (549 дней)
```

### Season 1
```
Период:    2026-01-06  →  2026-01-15
Длина:     10 дней
Дни:       День 1-10
```

### Season 2
```
Период:    2026-01-16  →  2026-01-25
Длина:     10 дней
Дни:       День 1-10
```

### Season 3
```
Период:    2026-01-26  →  2026-02-04
Длина:     10 дней
Дни:       День 1-10
```

### И так далее...
Каждый сезон = **10 последовательных дней**

---

## 🔄 Автоматическое определение дат

### Метод 1: AUTO_SEASON в fetch_events_config.py

```python
# Включить автоматическое определение дат
AUTO_SEASON = True
```

При `AUTO_SEASON = True`:
- Скрипт автоматически определяет текущий сезон
- Применяет соответствующие даты для фильтрации
- Genesis: 2024-07-06 до 2026-01-05
- Season1+: автоматически вычисляет границы

### Метод 2: Через daily_scheduler.py

```bash
# Scheduler автоматически настраивает даты
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

Daily Scheduler:
1. Определяет текущий сезон
2. Настраивает `fetch_events_config.py`
3. Запускает скрипты с правильными датами

---

## 📊 Примеры использования

### Загрузить Genesis полностью

```python
# В fetch_events_config.py
from datetime import datetime

AUTO_SEASON = False  # Отключить авто-режим
START_DATE = datetime(2024, 7, 6)   # Genesis start
END_DATE = datetime(2026, 1, 5)     # Genesis end
CLOSED_ONLY = True
MIN_VOLUME = 100_000_000  # 100M для Genesis
```

```bash
# Запустить
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local
```

### Загрузить конкретный сезон

```python
# Season 1: 2026-01-06 to 2026-01-15
from datetime import datetime

AUTO_SEASON = False
START_DATE = datetime(2026, 1, 6)
END_DATE = datetime(2026, 1, 15)
```

### Автоматический режим

```python
# В fetch_events_config.py
AUTO_SEASON = True  # Включить авто-режим
# START_DATE и END_DATE игнорируются
```

```bash
# Scheduler сам настроит даты для текущего сезона
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

---

## 🗄️ SQL запросы по сезонам

### Проверить какие сезоны загружены

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Все сезоны
SELECT 
    season_name,
    start_date,
    end_date,
    season_type
FROM seasons
ORDER BY start_date;
```

Ожидаемый результат:
```
 season_name |  start_date  |   end_date   | season_type 
-------------+--------------+--------------+-------------
 genesis     | 2024-07-06   | 2026-01-05   | genesis
 season1     | 2026-01-06   | 2026-01-15   | regular
 season2     | 2026-01-16   | 2026-01-25   | regular
 ...
```

### Статистика по сезонам

```sql
-- Статус всех сезонов
SELECT * FROM current_season_status;

-- События по сезонам
SELECT 
    s.season_name,
    COUNT(DISTINCT e.id) as events_count,
    SUM(e.volume) as total_volume
FROM seasons s
LEFT JOIN events e ON 
    e.end_date::date BETWEEN s.start_date AND s.end_date
GROUP BY s.season_name
ORDER BY s.start_date;
```

---

## 🔧 Конфигурация для разных сценариев

### Сценарий 1: Первая загрузка (Genesis)

```python
# fetch_events_config.py
AUTO_SEASON = False
START_DATE = datetime(2024, 7, 6)
END_DATE = datetime(2026, 1, 5)
CLOSED_ONLY = True
MIN_VOLUME = 100_000_000  # 100M для исторических данных
```

### Сценарий 2: Ежедневная автоматическая загрузка

```python
# fetch_events_config.py
AUTO_SEASON = True  # Scheduler сам настроит даты и MIN_VOLUME
CLOSED_ONLY = True
# MIN_VOLUME будет установлен автоматически:
# - Genesis: 100M
# - Seasons: 5M
```

```bash
# Запускается автоматически каждый день в 2:00 UTC
# Или вручную:
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

### Сценарий 3: Тестирование на нескольких днях

```python
# fetch_events_config.py
AUTO_SEASON = False
START_DATE = datetime(2026, 1, 6)
END_DATE = datetime(2026, 1, 8)  # Только 3 дня
MAX_EVENTS = 1000  # Лимит для теста
```

---

## 📈 Визуализация временной линии

```
2024-07-06                                           2026-01-05
    │◄──────────────── GENESIS (549 дней) ──────────────►│
    │                                                      │
    └──────────────────────────────────────────────────────┘
                                                            │
                                                            ↓
                                                      2026-01-06
                                                            │
    ┌───────────────────────────────────────────────────────┤
    │                                                       │
    │  Season 1    Season 2    Season 3    Season 4   ...  │
    │  10 дней     10 дней     10 дней     10 дней         │
    │  01.06-15    01.16-25    01.26-02.04  02.05-14       │
    │                                                       │
    └───────────────────────────────────────────────────────►
                        Бесконечно...
```

---

## ⚡ Важные замечания

### 1. Закрытые события (CLOSED_ONLY = True)
Загружаются только события с `closed = True`. Это означает:
- События должны быть завершены
- Есть финальный результат
- UMA resolution = "Resolved"

### 2. Минимальный объем (MIN_VOLUME)
Фильтр по объему торгов **зависит от сезона**:

**Genesis (исторические данные):**
- `MIN_VOLUME = 100_000_000` (100M USD)
- Только крупные исторические события
- Снижает объем данных для хранения

**Season 1+ (текущие сезоны):**
- `MIN_VOLUME = 5_000_000` (5M USD)
- Более детальные текущие данные
- Охватывает больше активных рынков

**При `AUTO_SEASON = True`:**
- Автоматически применяется правильный фильтр
- Genesis → 100M, Seasons → 5M

### 3. Даты событий (end_date vs created_at)
События фильтруются по `end_date`:
- `end_date` = когда событие закрылось
- Если событие создано в 2024, но закрылось в 2026 → попадет в Season1+
- Genesis включает все события, закрытые до 2026-01-05

### 4. Историческая загрузка
При первом запуске:
- Проверяется наличие данных Genesis
- Если данных нет → автоматически предлагается загрузка
- Можно загрузить вручную с `START_DATE = datetime(2024, 7, 6)`

---

## 🔍 Отладка и проверка дат

### Проверить какой сейчас сезон

```bash
docker exec polystars_scheduler python /app/scripts/season_manager.py --status
```

### Проверить сезон для конкретной даты

```bash
docker exec polystars_scheduler python /app/scripts/season_manager.py --check-date 2026-01-10
```

Вывод:
```
Date 2026-01-10:
  Season: season1
  Type: regular
  Day: 5
```

### Проверить текущий config

```bash
docker exec polystars_python python scripts/fetch/fetch_events_config_loader.py --summary
```

Вывод:
```
📊 Current Config:
   • auto_season: True
   • start_date: 2026-01-06T00:00:00
   • end_date: 2026-01-15T23:59:59
   • min_volume: 100000000
   • closed_only: True
   • resolution_status: Resolved
```

---

## 📚 Связанные файлы

- `scripts/season_manager.py` - Логика сезонов
- `scripts/fetch/fetch_events_config.py` - Конфигурация фильтров
- `scripts/fetch/fetch_events_config_loader.py` - Применение дат сезона
- `scripts/daily_scheduler.py` - Оркестрация загрузки
- `sql/schemas/add_season_tables.sql` - Таблицы сезонов

---

**Версия:** 1.0  
**Дата:** 2026-02-09  
**Обновления:** Установлены правильные даты Genesis (2024-07-06 → 2026-01-05)
