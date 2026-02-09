# 📅 Автоматическая загрузка данных с сезонами

## 🎯 Обзор

Система автоматической загрузки данных с поддержкой сезонов для PolyStars.

### Основные возможности

- ✅ **Автоматический запуск 1 раз в сутки** (каждый день в 2:00 UTC)
- ✅ **Система сезонов** (genesis + season1, season2, ... по 10 дней)
- ✅ **Историческая загрузка** данных при первом запуске
- ✅ **Отслеживание прогресса** загрузки по дням и скриптам
- ✅ **Автоматическое восстановление** пропущенных дней

---

## 📊 Структура сезонов

```
genesis     → Исторические данные (до 2026-02-09)
season1     → 2026-02-10 до 2026-02-19 (10 дней)
season2     → 2026-02-20 до 2026-03-01 (10 дней)
season3     → 2026-03-02 до 2026-03-11 (10 дней)
...
```

Каждый сезон отслеживает:
- ✅ Какие скрипты выполнены (events, redemptions, positions, leaderboard)
- 📅 Дата загрузки для каждого дня сезона
- 📈 Количество загруженных записей
- ❌ Ошибки выполнения

---

## 🚀 Быстрый старт

### 1. Запуск с Docker Compose

```bash
# Запустить все сервисы (включая scheduler)
docker-compose up -d

# Проверить статус
docker-compose ps
```

Scheduler автоматически:
1. Создаст таблицы для отслеживания сезонов
2. Проверит текущий сезон
3. Запустится с cron (ежедневно в 2:00 UTC)

### 2. Ручной запуск планировщика

```bash
# Запустить загрузку данных вручную
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Проверить статус сезона
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Проверить статус планировщика
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --status
```

### 3. Просмотр логов

```bash
# Логи scheduler (cron + все скрипты)
docker logs polystars_scheduler

# Логи файла (детальные)
docker exec polystars_scheduler tail -f /app/logs/scheduler.log
```

---

## 📋 Работа с сезонами

### Season Manager

```bash
# Проверить текущий сезон
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Проверить сезон для конкретной даты
docker exec polystars_scheduler python /app/scripts/season_manager.py --check-date 2026-02-15

# Показать пропущенные дни
docker exec polystars_scheduler python /app/scripts/season_manager.py --missing

# Отметить скрипт как выполненный
docker exec polystars_scheduler python /app/scripts/season_manager.py --mark-loaded events
```

### Daily Scheduler

```bash
# Запустить полный pipeline
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Принудительная перезагрузка (даже если данные есть)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force

# Тестовый запуск (без выполнения)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --dry-run

# Загрузить исторические данные (genesis)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --historical
```

---

## 🔄 Workflow загрузки данных

Ежедневно в 2:00 UTC выполняется:

```
1. Проверка сезона
   ↓
2. Проверка: нужна ли загрузка данных за сегодня?
   ↓
3. STEP 1: Events
   → scripts/fetch/fetch_events_parallel_optimized.py --upload --local
   ↓
4. STEP 2: Redemptions
   → scripts/fetch/fetch_redemptions.py --upload --local
   ↓
5. STEP 3: User Closed Positions
   → scripts/fetch/fetch_user_closed_positions_parallel.py --upload --local
   ↓
6. STEP 4: Trader Leaderboard
   → scripts/fetch/fetch_trader_leaderboard_parallel.py --upload --local --from-db
   ↓
7. Отметка выполнения в БД
   ↓
8. Готово! ✅
```

---

## 🗄️ Таблицы в БД

### `seasons`
Информация о сезонах:
```sql
SELECT * FROM seasons ORDER BY start_date DESC;
```

Поля:
- `season_name` - Имя сезона (genesis, season1, season2, ...)
- `start_date` - Дата начала
- `end_date` - Дата окончания
- `season_type` - Тип (genesis или regular)

### `season_data_loads`
Отслеживание ежедневных загрузок:
```sql
SELECT * FROM season_data_loads 
WHERE season_name = 'season1' 
ORDER BY load_date DESC;
```

Поля:
- `season_name` - Сезон
- `load_date` - Дата загрузки
- `day_in_season` - День в сезоне (1-10)
- `events_loaded`, `redemptions_loaded`, `positions_loaded`, `leaderboard_loaded` - Статусы
- `events_count`, `redemptions_count`, ... - Количество записей
- Timestamps для каждого скрипта

### Views (полезные представления)

```sql
-- Статус всех сезонов
SELECT * FROM current_season_status;

-- Статус за последние 30 дней
SELECT * FROM daily_load_status;
```

---

## ⚙️ Настройка расписания

### Изменить время запуска

Отредактируйте `scripts/cron/setup_cron.sh`:

```bash
# Текущее: каждый день в 2:00 UTC
CRON_SCHEDULE="0 2 * * *"

# Примеры:
# Каждый день в 3:30 UTC
CRON_SCHEDULE="30 3 * * *"

# Дважды в день (2:00 и 14:00)
CRON_SCHEDULE="0 2,14 * * *"

# Каждые 6 часов
CRON_SCHEDULE="0 */6 * * *"
```

После изменения:
```bash
docker-compose restart scheduler
```

### Отключить автоматический запуск

```bash
# Остановить scheduler
docker-compose stop scheduler

# Или исключить из запуска
docker-compose up -d postgres python_scripts
```

---

## 🛠️ Устранение неполадок

### Проверить работу cron

```bash
# Проверить, что cron работает
docker exec polystars_scheduler ps aux | grep cron

# Проверить crontab
docker exec polystars_scheduler crontab -l

# Проверить логи cron
docker exec polystars_scheduler tail -f /var/log/cron.log
```

### Если данные не загружаются

1. **Проверить статус сезона:**
```bash
docker exec polystars_scheduler python /app/scripts/season_manager.py --status
```

2. **Проверить подключение к БД:**
```bash
docker exec polystars_scheduler python /app/scripts/db/test_db_connection.py
```

3. **Запустить вручную с подробным выводом:**
```bash
docker exec -it polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

4. **Проверить логи:**
```bash
docker logs polystars_scheduler --tail 100
```

### Пересоздать scheduler

```bash
# Остановить и удалить
docker-compose stop scheduler
docker-compose rm -f scheduler

# Пересобрать и запустить
docker-compose build scheduler
docker-compose up -d scheduler
```

---

## 📚 Структура файлов

```
PolyStars/
├── scripts/
│   ├── season_manager.py          # Менеджер сезонов
│   ├── daily_scheduler.py         # Главный планировщик
│   ├── cron/
│   │   ├── setup_cron.sh         # Настройка cron
│   │   └── docker-entrypoint-scheduler.sh  # Entrypoint для Docker
│   └── fetch/                     # Скрипты загрузки данных
│       ├── fetch_events_parallel_optimized.py
│       ├── fetch_redemptions.py
│       ├── fetch_user_closed_positions_parallel.py
│       └── fetch_trader_leaderboard_parallel.py
├── sql/
│   └── schemas/
│       ├── init-db.sql           # Основные таблицы
│       └── add_season_tables.sql # Таблицы сезонов
├── Dockerfile.scheduler           # Docker образ для scheduler
├── docker-compose.yml            # Конфигурация Docker
└── logs/
    └── scheduler.log             # Логи выполнения
```

---

## 🎯 Примеры использования

### Загрузить данные за конкретную дату

```bash
# Через Python (TODO: добавить поддержку --date)
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py --upload --local
```

### Восстановить пропущенные дни

```bash
# 1. Посмотреть пропущенные дни
docker exec polystars_scheduler python /app/scripts/season_manager.py --missing

# 2. Запустить с --force для перезагрузки
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force
```

### Мониторинг прогресса

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Статус всех сезонов
SELECT * FROM current_season_status;

-- Последние 10 дней загрузки
SELECT 
    load_date,
    season_name,
    day_in_season,
    events_loaded,
    redemptions_loaded,
    positions_loaded,
    leaderboard_loaded,
    events_count + redemptions_count + positions_count + leaderboard_count as total_records
FROM season_data_loads
ORDER BY load_date DESC
LIMIT 10;
```

---

## 📊 Dashboard статистики

```sql
-- Общая статистика по всем сезонам
SELECT 
    season_name,
    COUNT(DISTINCT load_date) as days_with_data,
    SUM(events_count) as total_events,
    SUM(redemptions_count) as total_redemptions,
    SUM(positions_count) as total_positions,
    SUM(leaderboard_count) as total_leaderboard,
    MIN(load_date) as first_load,
    MAX(load_date) as last_load
FROM season_data_loads
GROUP BY season_name
ORDER BY season_name;

-- Процент завершенности текущего сезона
WITH current_season AS (
    SELECT 
        season_name,
        COUNT(*) as days_loaded,
        COUNT(*) FILTER (WHERE 
            events_loaded AND 
            redemptions_loaded AND 
            positions_loaded AND 
            leaderboard_loaded
        ) as days_complete
    FROM season_data_loads
    WHERE season_name = (
        SELECT season_name FROM seasons 
        WHERE CURRENT_DATE BETWEEN start_date AND end_date
    )
    GROUP BY season_name
)
SELECT 
    season_name,
    days_complete,
    days_loaded,
    ROUND(days_complete::numeric / NULLIF(days_loaded, 0) * 100, 2) as completion_percent
FROM current_season;
```

---

## ✅ Checklist установки

- [ ] Docker и Docker Compose установлены
- [ ] `.env` файл настроен с DATABASE параметрами
- [ ] `docker-compose up -d` запущен
- [ ] Таблицы сезонов созданы (автоматически)
- [ ] Season manager показывает статус: `docker exec polystars_scheduler python /app/scripts/season_manager.py --status`
- [ ] Cron работает: `docker exec polystars_scheduler crontab -l`
- [ ] Тестовый запуск успешен: `docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --dry-run`

---

**Версия:** 1.0  
**Дата:** 2026-02-09
