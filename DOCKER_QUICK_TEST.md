# 🐳 Быстрое тестирование через Docker

## 📋 Предварительные условия
- Docker уже запущен: `docker-compose up -d`
- БД инициализирована с таблицей `data_loads`

---

## 🚀 Быстрый тест (5-10 минут)

### Шаг 1: Проверить состояние системы
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
```

**Ожидаемый вывод:**
```
❌ Genesis not loaded
👉 Run: --historical
```

---

### Шаг 2: Dry-run Genesis (проверка логики)
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical --dry-run
```

**Что проверяет:**
- ✅ Конфигурация дат правильная
- ✅ Скрипты найдены
- ✅ Нет ошибок в коде
- ❌ НЕ загружает данные

**Время:** ~5 секунд

---

### Шаг 3: Загрузить Genesis (реальные данные)
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical
```

**Что происходит:**
```
🕰️ GENESIS DATA LOAD
Period: 2024-07-06 to 2026-01-05
Filter: 100M volume

🚀 RUNNING: Events Fetcher
[~5-10 минут]

🚀 RUNNING: Redemptions Fetcher  
[~5-10 минут]

🚀 RUNNING: User Closed Positions
[~10-15 минут]

🚀 RUNNING: Trader Leaderboard
[~5-10 минут]

📊 GENESIS LOAD SUMMARY
✅ Successful: 4/4
```

**Время:** ~25-40 минут

**⚠️ Для быстрого теста:** См. раздел "Ускоренное тестирование" ниже

---

### Шаг 4: Проверить результат Genesis
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
```

**Ожидаемый вывод:**
```
✅ Genesis loaded - ready for daily operations

Today's Loading Dates (2026-02-10):
  • Events: 2026-02-09 (yesterday)
  • Redemptions: 2026-02-07 (3 days ago)

Today's Status:
  • Events: ⏳ Pending
  • Redemptions: ⏳ Pending
```

---

### Шаг 5: Запустить Daily загрузку
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run
```

**Что происходит:**
```
📅 STEP 1: Events for 2026-02-09
✅ Loaded 15 events

📅 STEP 2: Redemptions/Positions/Leaderboard for 2026-02-07
✅ Loaded

📊 PIPELINE SUMMARY
✅ Successful: 4/4
```

**Время:** ~3-5 минут

---

### Шаг 6: Проверить финальное состояние
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
```

**Ожидаемый вывод:**
```
✅ Genesis loaded
✅ Events (2026-02-09): Loaded
✅ Redemptions (2026-02-07): Loaded
```

---

## ⚡ Ускоренное тестирование (5-7 минут)

### Вариант 1: Временно изменить даты Genesis

```bash
# Подключиться к контейнеру
docker exec -it polystars_scheduler bash

# Открыть файл
nano /app/scripts/data_loading_manager.py

# Найти строки (около строки 9-10):
GENESIS_START_DATE = date(2024, 7, 6)
GENESIS_END_DATE = date(2026, 1, 5)

# Заменить на (5 дней вместо 1.5 лет):
GENESIS_START_DATE = date(2026, 1, 1)
GENESIS_END_DATE = date(2026, 1, 5)

# Сохранить: Ctrl+O, Enter, Ctrl+X

# Запустить Genesis
python /app/scripts/daily_scheduler_simple.py --historical

# Выйти
exit
```

**Время:** ~2-3 минуты для Genesis

---

### Вариант 2: Ограничить количество событий

```bash
# Подключиться
docker exec -it polystars_scheduler bash

# Открыть конфиг
nano /app/scripts/fetch/fetch_events_config.py

# Найти (около строки 50-55):
MIN_VOLUME = 5_000_000

# Заменить на (меньше событий):
MIN_VOLUME = 50_000_000  # 50M вместо 5M

# Также добавить лимит (после MIN_VOLUME):
EVENT_LIMIT = 50  # Максимум 50 событий

# Сохранить и выйти
exit
```

---

## 🗄️ Проверка данных в БД

### Подключиться к PostgreSQL
```bash
docker exec -it polystars_postgres psql -U postgres -d polymarket
```

### Полезные запросы

#### 1. Проверить таблицу data_loads
```sql
SELECT * FROM data_loads ORDER BY load_date DESC LIMIT 10;
```

#### 2. Проверить view recent_loads
```sql
SELECT * FROM recent_loads;
```

#### 3. Статистика Genesis
```sql
SELECT * FROM genesis_status;
```

#### 4. Сколько событий загружено
```sql
SELECT COUNT(*) FROM events;
SELECT COUNT(*) FROM redemptions;
SELECT COUNT(*) FROM user_closed_positions;
SELECT COUNT(*) FROM trader_leaderboard;
```

#### 5. События по датам
```sql
SELECT 
    end_date::date,
    COUNT(*) as events_count,
    SUM(volume)::bigint as total_volume
FROM events
GROUP BY end_date::date
ORDER BY end_date::date DESC
LIMIT 10;
```

#### 6. Выйти из psql
```sql
\q
```

---

## 🔍 Мониторинг логов

### Смотреть логи в реальном времени
```bash
# Scheduler логи
docker logs -f polystars_scheduler

# PostgreSQL логи
docker logs -f polystars_postgres

# Все контейнеры
docker-compose logs -f
```

### Последние 100 строк
```bash
docker logs --tail 100 polystars_scheduler
```

---

## 🧪 Тестовые сценарии

### Тест 1: Проверка дублей (первые 3 дня после Genesis)
```bash
# 1. Загрузить Genesis
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical

# 2. Запустить Day 1
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run

# 3. Проверить что redemptions пропущены
docker logs polystars_scheduler | grep "Skipping redemptions"
# Должно быть: "⏭️ Skipping (Genesis period)"

# 4. Проверить data_loads
docker exec -it polystars_postgres psql -U postgres -d polymarket -c "SELECT load_date, events_loaded, redemptions_loaded FROM data_loads ORDER BY load_date DESC LIMIT 5;"
```

### Тест 2: Проверка фильтрации по датам
```bash
# Проверить что redemptions читает только события за свою дату
docker logs polystars_scheduler | grep "Filter: Events from"
# Должно быть: "🔍 Filter: Events from 2026-XX-XX"
```

### Тест 3: Catch-up (пропущенные дни)
```bash
# Dry-run
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up --dry-run

# Реальная загрузка
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up
```

---

## 🛠️ Полезные команды

### Перезапуск контейнеров
```bash
docker-compose restart
```

### Остановить всё
```bash
docker-compose down
```

### Запустить заново
```bash
docker-compose up -d
```

### Очистить БД (начать с нуля)
```bash
docker-compose down -v  # Удалит volumes (все данные!)
docker-compose up -d    # Пересоздаст БД
```

### Проверить статус контейнеров
```bash
docker-compose ps
```

### Использование ресурсов
```bash
docker stats
```

---

## 🎯 Быстрый чек-лист для теста

- [ ] `docker-compose up -d` - запустить
- [ ] `--check` - проверить состояние
- [ ] `--historical --dry-run` - проверить логику Genesis
- [ ] `--historical` - загрузить Genesis (или с укороченными датами)
- [ ] `--check` - проверить что Genesis загружен
- [ ] `--run` - запустить daily
- [ ] `--check` - проверить что daily загружен
- [ ] Проверить данные в БД через psql
- [ ] Проверить логи на ошибки
- [ ] `--catch-up` - проверить догрузку пропусков

---

## ⚠️ Частые проблемы

### 1. "Genesis not loaded"
**Решение:** Запустить `--historical`

### 2. "Connection refused" к PostgreSQL
**Решение:** 
```bash
docker-compose ps  # Проверить что postgres запущен
docker-compose restart postgres
```

### 3. Долго загружается Genesis
**Решение:** Использовать ускоренное тестирование (короткие даты или высокий MIN_VOLUME)

### 4. "Already loaded"
**Решение:** Использовать `--force` для перезагрузки
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run --force
```

### 5. Нет таблицы data_loads
**Решение:**
```bash
# Пересоздать БД
docker-compose down -v
docker-compose up -d

# Или добавить вручную
docker exec -it polystars_postgres psql -U postgres -d polymarket -f /docker-entrypoint-initdb.d/init-db.sql
```

---

## 📊 Ожидаемое время выполнения

| Операция | Полный Genesis | Укороченный Genesis (5 дней) |
|----------|----------------|------------------------------|
| **--check** | 1 сек | 1 сек |
| **--historical --dry-run** | 5 сек | 5 сек |
| **--historical** | 25-40 мин | 2-3 мин |
| **--run (daily)** | 3-5 мин | 30-60 сек |
| **--catch-up (10 дней)** | 30-50 мин | 5-10 мин |

---

## ✅ Успешный тест выглядит так:

```bash
$ docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check

🔍 SYSTEM STATE CHECK
======================================================================

Database Status:
  • Has data: Yes
  • Genesis loaded: Yes ✅

✅ Genesis loaded - ready for daily operations

Today's Loading Dates (2026-02-10):
  • Events: 2026-02-09 (yesterday)
  • Redemptions: 2026-02-07 (3 days ago)

Today's Status:
  • Events (2026-02-09): ✅ Loaded
  • Redemptions (2026-02-07): ✅ Loaded

======================================================================
```

🎉 **Система работает!**

---

**Версия:** 1.0  
**Дата:** 2026-02-10  
**Для:** Быстрого тестирования системы через Docker
