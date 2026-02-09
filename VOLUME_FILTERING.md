# 💰 Фильтрация по объему в зависимости от сезона

## 📊 Два уровня фильтрации

### Genesis (Исторические данные)
```
Период:      2024-07-06 → 2026-01-05
MIN_VOLUME:  100,000,000 USD (100M)
Причина:     Только крупные исторические события
```

**Зачем 100M для Genesis?**
- 📦 Уменьшает объем исторических данных
- 🎯 Фокус на значимых событиях
- ⚡ Быстрая загрузка и обработка
- 💾 Экономия места в БД

### Season 1+ (Текущие сезоны)
```
Период:      С 2026-01-06 (по 10 дней каждый)
MIN_VOLUME:  5,000,000 USD (5M)
Причина:     Более детальные текущие данные
```

**Зачем 5M для сезонов?**
- 📈 Больше актуальных рынков
- 🔍 Детальная картина активности
- 📊 Лучше для анализа трендов
- 🎮 Охват средних по размеру событий

---

## 🔄 Автоматическое применение

### При AUTO_SEASON = True

```python
# В fetch_events_config.py
AUTO_SEASON = True
```

Scheduler автоматически установит:

| Сезон | MIN_VOLUME | Описание |
|-------|------------|----------|
| genesis | 100,000,000 | 100M для исторических данных |
| season1 | 5,000,000 | 5M для текущих сезонов |
| season2 | 5,000,000 | 5M для текущих сезонов |
| season3 | 5,000,000 | 5M для текущих сезонов |
| ... | 5,000,000 | 5M для всех сезонов |

### При AUTO_SEASON = False

```python
# Ручная настройка
AUTO_SEASON = False

# Genesis
MIN_VOLUME = 100_000_000  # 100M
START_DATE = datetime(2024, 7, 6)
END_DATE = datetime(2026, 1, 5)

# Или Season
MIN_VOLUME = 5_000_000  # 5M
START_DATE = datetime(2026, 1, 6)
END_DATE = datetime(2026, 1, 15)
```

---

## 📈 Сравнение результатов

### Genesis с 100M фильтром

```sql
-- Ожидаемое количество событий
SELECT COUNT(*) FROM events 
WHERE volume >= 100000000
  AND end_date::date BETWEEN '2024-07-06' AND '2026-01-05'
  AND closed = true;

-- Примерно: 50-100 крупных событий
```

**Типичные события:**
- Президентские выборы ($500M+)
- Крупные спортивные события (Super Bowl, World Cup)
- Макроэкономические события
- Криптовалютные прогнозы на топовые монеты

### Season с 5M фильтром

```sql
-- Ожидаемое количество событий за 10 дней
SELECT COUNT(*) FROM events 
WHERE volume >= 5000000
  AND end_date::date BETWEEN '2026-01-06' AND '2026-01-15'
  AND closed = true;

-- Примерно: 20-50 событий за сезон
```

**Типичные события:**
- Средние политические события
- Спортивные матчи топ-лиг
- Корпоративные события (IPO, earnings)
- Криптовалютные события средней значимости

---

## 🎯 Рекомендации

### Для Genesis (исторические данные)

✅ **Используйте 100M если:**
- Первая загрузка данных
- Нужны только крупные события
- Ограниченное место в БД
- Фокус на значимых исторических событиях

❌ **Не используйте 100M если:**
- Нужен полный исторический архив
- Анализ требует мелких событий
- Есть достаточно места для хранения

### Для Season (текущие данные)

✅ **Используйте 5M если:**
- Ежедневная автоматическая загрузка
- Нужна детальная картина рынка
- Анализ активных трендов
- Мониторинг средних событий

⚠️ **Можно использовать 1M-3M если:**
- Нужен максимум детализации
- Есть достаточные ресурсы
- Готовы обрабатывать больше данных

---

## 🔧 Настройка MIN_VOLUME

### Вручную в config

```python
# scripts/fetch/fetch_events_config.py

# Genesis
MIN_VOLUME = 100_000_000  # 100M

# Или Season
MIN_VOLUME = 5_000_000  # 5M

# Или кастомное значение
MIN_VOLUME = 10_000_000  # 10M
```

### Через config_loader

```python
from scripts.fetch.fetch_events_config_loader import apply_season_dates
import scripts.fetch.fetch_events_config as config

# Применить сезонные настройки (включая MIN_VOLUME)
apply_season_dates()

# Или переопределить вручную
config.MIN_VOLUME = 50_000_000  # Кастомное значение
```

### В daily_scheduler

```python
# Scheduler автоматически применяет правильный MIN_VOLUME
# На основе типа сезона (genesis или regular)
```

---

## 📊 SQL для анализа

### События по диапазонам объема

```sql
-- Распределение событий по объему (Genesis период)
SELECT 
    CASE 
        WHEN volume >= 1000000000 THEN '1B+'
        WHEN volume >= 500000000 THEN '500M-1B'
        WHEN volume >= 100000000 THEN '100M-500M'
        WHEN volume >= 50000000 THEN '50M-100M'
        WHEN volume >= 10000000 THEN '10M-50M'
        WHEN volume >= 5000000 THEN '5M-10M'
        ELSE '<5M'
    END as volume_range,
    COUNT(*) as event_count,
    ROUND(AVG(volume)::numeric, 0) as avg_volume
FROM events
WHERE end_date::date BETWEEN '2024-07-06' AND '2026-01-05'
  AND closed = true
GROUP BY volume_range
ORDER BY MIN(volume) DESC;
```

### Топ-10 событий Genesis

```sql
-- Самые крупные события Genesis
SELECT 
    title,
    volume,
    end_date::date,
    (SELECT COUNT(*) FROM markets m WHERE m.event_id = e.id) as markets_count
FROM events e
WHERE volume >= 100000000
  AND end_date::date BETWEEN '2024-07-06' AND '2026-01-05'
  AND closed = true
ORDER BY volume DESC
LIMIT 10;
```

### Сравнение сезонов

```sql
-- Среднее количество событий по сезонам
SELECT 
    s.season_name,
    s.season_type,
    COUNT(DISTINCT sdl.load_date) as days_loaded,
    SUM(sdl.events_count) as total_events,
    ROUND(SUM(sdl.events_count)::numeric / NULLIF(COUNT(DISTINCT sdl.load_date), 0), 1) as avg_per_day
FROM seasons s
LEFT JOIN season_data_loads sdl ON s.season_name = sdl.season_name
GROUP BY s.season_name, s.season_type
ORDER BY s.season_name;
```

---

## ⚡ Производительность

### Genesis (100M фильтр)

| Метрика | Значение |
|---------|----------|
| События | ~50-100 |
| Время загрузки | 5-10 минут |
| Размер JSON | ~50-100 MB |
| Записей в БД | ~50-100 events + markets |

### Season (5M фильтр)

| Метрика | Значение |
|---------|----------|
| События за 10 дней | ~20-50 |
| Время загрузки | 2-5 минут |
| Размер JSON | ~20-50 MB |
| Записей в БД | ~20-50 events + markets |

---

## 🎓 Примеры

### Пример 1: Загрузить Genesis с 100M

```bash
# 1. Настроить config
# AUTO_SEASON = False
# MIN_VOLUME = 100_000_000
# START_DATE = datetime(2024, 7, 6)
# END_DATE = datetime(2026, 1, 5)

# 2. Запустить
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local

# 3. Проверить
docker exec polystars_postgres psql -U postgres -d polymarket -c "
SELECT COUNT(*), MIN(volume), MAX(volume), AVG(volume)::bigint
FROM events
WHERE end_date::date BETWEEN '2024-07-06' AND '2026-01-05';
"
```

### Пример 2: Автоматическая загрузка с правильными фильтрами

```bash
# 1. Включить AUTO_SEASON
# AUTO_SEASON = True

# 2. Запустить scheduler
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Scheduler сам определит:
# - Genesis → MIN_VOLUME = 100M
# - Season1+ → MIN_VOLUME = 5M
```

### Пример 3: Кастомный фильтр для тестирования

```python
# fetch_events_config.py
AUTO_SEASON = False
MIN_VOLUME = 10_000_000  # 10M для теста
START_DATE = datetime(2026, 1, 6)
END_DATE = datetime(2026, 1, 8)  # Только 3 дня
MAX_EVENTS = 100  # Лимит для теста
```

---

## ✅ Checklist

- [ ] Понимаете разницу между Genesis (100M) и Season (5M)
- [ ] Настроен `AUTO_SEASON = True` для автоматического режима
- [ ] Или вручную установлен правильный `MIN_VOLUME`
- [ ] Проверили результаты в БД после загрузки
- [ ] Убедились что объем событий соответствует ожиданиям

---

**Версия:** 1.0  
**Дата:** 2026-02-09  
**Важно:** Genesis = 100M, Seasons = 5M
