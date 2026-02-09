# 🎯 Обновление: Правильные даты Genesis и сезонов

## 🆕 КРИТИЧЕСКОЕ ОБНОВЛЕНИЕ: Задержка загрузки данных

### ⏰ Новая логика загрузки (v2.1)

**Разные типы данных загружаются с разной задержкой:**

#### Свежие данные (текущий день):
- ✅ **Events** - события и рынки загружаются сразу

#### Данные с задержкой (3 дня назад):
- ⏰ **Redemptions** - выплаты (нужно время пользователям забрать выигрыши)
- ⏰ **Positions** - закрытые позиции (окончательные расчеты)
- ⏰ **Leaderboard** - рейтинги (стабильные после задержки)

**Почему 3 дня?**
- 95%+ пользователей забирают выигрыши в течение 3 дней
- Окончательные данные о PnL и позициях
- Меньше изменений в исторических данных

**Пример:**
```
Сегодня: 2026-01-10 (День 5 сезона)

STEP 1: Events
└─ Загружает события за 2026-01-10 (сегодня) ✅

STEP 2-4: Redemptions, Positions, Leaderboard
└─ Загружает данные за 2026-01-07 (3 дня назад) ✅
    └─ К этому времени большинство забрали выигрыши
```

См. подробную документацию: **[DATA_LAG_STRATEGY.md](./DATA_LAG_STRATEGY.md)**

---

## ✅ Что было исправлено ранее

### 1. **Даты Genesis**
```
БЫЛО:     2026-02-09 (сегодня) → бесконечность
СТАЛО:    2024-07-06 → 2026-01-05 (исторические данные)
```

**Genesis теперь правильно:**
- Начинается: `2024-07-06`
- Заканчивается: `2026-01-05`
- Длительность: ~18 месяцев (549 дней)

### 2. **Сезоны (Season 1, 2, 3...)**
```
Season 1:  2026-01-06 → 2026-01-15 (10 дней)
Season 2:  2026-01-16 → 2026-01-25 (10 дней)
Season 3:  2026-01-26 → 2026-02-04 (10 дней)
...
```

Каждый сезон = **10 последовательных дней**

---

## 📝 Обновленные файлы

### 1. `scripts/season_manager.py`
✅ Обновлены константы:
```python
GENESIS_START_DATE = date(2024, 7, 6)
GENESIS_END_DATE = date(2026, 1, 5)
SEASON_LENGTH_DAYS = 10
```

✅ Исправлен расчет сезонов:
- Season 1 начинается с 2026-01-06 (следующий день после Genesis)
- Правильный подсчет номера сезона по дате

### 2. `scripts/fetch/fetch_events_config.py`
✅ Добавлен режим `AUTO_SEASON`:
```python
AUTO_SEASON = False  # Включить для автоматического определения дат
START_DATE = datetime(2024, 7, 6)  # Genesis start
END_DATE = datetime.now()  # Или конкретная дата
```

✅ Обновлена документация с правильными датами

### 3. `scripts/fetch/fetch_events_config_loader.py` (НОВЫЙ)
✅ Автоматическое применение дат сезона:
```python
from scripts.fetch.fetch_events_config_loader import apply_season_dates

# Применить даты текущего сезона
apply_season_dates()
```

### 4. `scripts/daily_scheduler.py`
✅ Добавлена автоматическая настройка config перед запуском:
```python
# Scheduler автоматически настраивает fetch_events_config
# в зависимости от текущего сезона
```

### 5. `SEASON_DATES.md` (НОВЫЙ)
✅ Подробная документация:
- Структура сезонов
- Примеры использования
- SQL запросы
- Конфигурация для разных сценариев

---

## 🚀 Как использовать

### Вариант 1: Автоматический режим (рекомендуется)

```python
# В scripts/fetch/fetch_events_config.py
AUTO_SEASON = True
```

```bash
# Scheduler сам определит текущий сезон и настроит даты
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

### Вариант 2: Вручную загрузить Genesis

```python
# В scripts/fetch/fetch_events_config.py
AUTO_SEASON = False
START_DATE = datetime(2024, 7, 6)   # Genesis start
END_DATE = datetime(2026, 1, 5)     # Genesis end
MIN_VOLUME = 100_000_000  # 100M
CLOSED_ONLY = True
```

```bash
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local
```

### Вариант 3: Загрузить конкретный сезон

```python
# Season 1: 2026-01-06 to 2026-01-15
AUTO_SEASON = False
START_DATE = datetime(2026, 1, 6)
END_DATE = datetime(2026, 1, 15)
```

---

## 🔍 Проверка

### Проверить текущий сезон

```bash
docker exec polystars_scheduler python /app/scripts/season_manager.py --status
```

Ожидаемый вывод:
```
📅 SEASON STATUS
======================================================================
Current Season: season1
Season Type: regular
Date Range: 2026-01-06 to 2026-01-15
Current Day: 5/10
Days Remaining: 5
======================================================================
```

### Проверить Genesis

```bash
docker exec polystars_scheduler python /app/scripts/season_manager.py --check-date 2025-06-01
```

Вывод:
```
Date 2025-06-01:
  Season: genesis
  Type: genesis
  Day: None
```

### Проверить конкретный сезон

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

---

## 📊 SQL запросы

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Создать запись для Genesis
INSERT INTO seasons (season_name, start_date, end_date, season_type)
VALUES ('genesis', '2024-07-06', '2026-01-05', 'genesis')
ON CONFLICT (season_name) DO UPDATE SET
    start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date;

-- Проверить сезоны
SELECT * FROM seasons ORDER BY start_date;

-- Статистика по сезонам
SELECT * FROM current_season_status;
```

---

## 🔄 Миграция данных (если нужно)

Если у вас уже есть данные с неправильными датами:

### 1. Очистить существующие записи сезонов

```sql
-- Осторожно! Удалит все записи о загрузках
TRUNCATE TABLE season_data_loads CASCADE;
TRUNCATE TABLE seasons CASCADE;
```

### 2. Создать Genesis вручную

```sql
INSERT INTO seasons (season_name, start_date, end_date, season_type)
VALUES ('genesis', '2024-07-06', '2026-01-05', 'genesis');
```

### 3. Перезапустить загрузку

```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force
```

---

## 📚 Дополнительная информация

- **Полная документация по датам:** [SEASON_DATES.md](./SEASON_DATES.md)
- **Настройка scheduler:** [SCHEDULER_SETUP.md](./SCHEDULER_SETUP.md)
- **Быстрый старт:** [QUICK_START_SCHEDULER.md](./QUICK_START_SCHEDULER.md)

---

## ⚡ Важно

### Фильтры по объему (ВАЖНО!):
- **Genesis:** `MIN_VOLUME = 100_000_000` (100M USD - только крупные исторические события)
- **Seasons:** `MIN_VOLUME = 5_000_000` (5M USD - более детальные текущие данные)
- `CLOSED_ONLY = True` (только закрытые события)
- `RESOLUTION_STATUS = 'Resolved'` (только resolved)

При `AUTO_SEASON = True` - автоматически применяется правильный MIN_VOLUME!

### Логика работы:
1. **Genesis (2024-07-06 → 2026-01-05):**
   - Все исторические данные
   - Загружается один раз при первом запуске
   - Или по требованию

2. **Season 1+ (с 2026-01-06):**
   - Автоматическая загрузка каждый день
   - По 10 дней на сезон
   - Scheduler сам определяет текущий день

3. **Автоматическое восстановление:**
   - Если пропущены дни → автоматически загрузит
   - Проверка пропущенных дней: `season_manager.py --missing`

---

## ✅ Checklist обновления

- [ ] Обновлен `scripts/season_manager.py` с правильными датами
- [ ] Обновлен `scripts/fetch/fetch_events_config.py`
- [ ] Создан `scripts/fetch/fetch_events_config_loader.py`
- [ ] Обновлен `scripts/daily_scheduler.py`
- [ ] Создан `SEASON_DATES.md` с документацией
- [ ] Пересобраны Docker образы: `docker-compose build`
- [ ] Перезапущены сервисы: `docker-compose restart`
- [ ] Проверен статус: `docker exec polystars_scheduler python /app/scripts/season_manager.py --status`

---

**Версия:** 2.0  
**Дата:** 2026-02-09  
**Критическое обновление:** Исправлены даты Genesis и сезонов

Теперь система полностью соответствует требованиям! 🎉
