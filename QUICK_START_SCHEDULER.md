# 🚀 Быстрый старт - Автоматическая загрузка данных

## Что нужно сделать?

### 1️⃣ Запустить все сервисы
```bash
docker-compose up -d
```

Это запустит:
- ✅ PostgreSQL базу данных
- ✅ Python контейнер для ручных скриптов
- ✅ **Scheduler** для автоматической загрузки (1 раз в сутки)

### 2️⃣ Проверить статус
```bash
# Проверить что все работает
docker-compose ps

# Проверить текущий сезон
docker exec polystars_scheduler python /app/scripts/season_manager.py --status
```

Вы увидите:
```
📅 SEASON STATUS
======================================================================
Current Season: season1
Season Type: regular
Date Range: 2026-02-10 to 2026-02-19
Current Day: 1/10
Days Remaining: 9

Today's Data Status (2026-02-10):
  • Events: ⏳ Pending
  • Redemptions: ⏳ Pending
  • Positions: ⏳ Pending
  • Leaderboard: ⏳ Pending
======================================================================
```

### 3️⃣ Запустить первую загрузку данных

**Вариант А: Автоматически (подождать до 2:00 UTC)**
```bash
# Scheduler запустится автоматически каждый день в 2:00 UTC
# Просто ждите!
```

**Вариант Б: Запустить вручную прямо сейчас**
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

Это выполнит последовательно:
1. ✅ Events (5-10 минут)
2. ✅ Redemptions (2-5 минут)
3. ✅ User Closed Positions (10-30 минут)
4. ✅ Trader Leaderboard (10-30 минут)

**Общее время: ~30-75 минут**

### 4️⃣ Проверить результат
```bash
# Проверить статус снова
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Проверить данные в БД
docker exec polystars_postgres psql -U postgres -d polymarket -c "
SELECT 
    COUNT(*) FILTER (WHERE TRUE) as total_events,
    COUNT(*) FILTER (WHERE closed = true) as closed_events
FROM events;
"
```

---

## 🎯 Что происходит дальше?

### Автоматическая загрузка
Каждый день в **2:00 UTC** scheduler автоматически:
1. Проверит текущий сезон и день
2. Если данные еще не загружены за этот день → загрузит
3. Если данные уже есть → пропустит

### Отслеживание прогресса

```bash
# Статус текущего сезона
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Логи выполнения
docker logs polystars_scheduler -f

# Детальные логи
docker exec polystars_scheduler tail -f /app/logs/scheduler.log
```

### SQL запросы

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
    leaderboard_loaded
FROM season_data_loads
ORDER BY load_date DESC
LIMIT 10;

-- Общая статистика
SELECT 
    season_name,
    COUNT(*) as days_loaded,
    SUM(events_count) as total_events,
    SUM(redemptions_count) as total_redemptions
FROM season_data_loads
GROUP BY season_name
ORDER BY season_name;
```

---

## 🛠️ Частые команды

```bash
# Запустить загрузку вручную
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Принудительная перезагрузка (даже если данные есть)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --force

# Тестовый запуск (без выполнения)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --dry-run

# Проверить статус
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --status

# Проверить cron
docker exec polystars_scheduler crontab -l

# Логи
docker logs polystars_scheduler
docker exec polystars_scheduler tail -f /app/logs/scheduler.log
```

---

## 📊 Структура сезонов

```
genesis     → Исторические данные (до 2026-02-09)
season1     → 2026-02-10 до 2026-02-19 (10 дней)
season2     → 2026-02-20 до 2026-03-01 (10 дней)
season3     → 2026-03-02 до 2026-03-11 (10 дней)
...
```

Каждый день сезона отслеживает:
- ✅ Events loaded?
- ✅ Redemptions loaded?
- ✅ Positions loaded?
- ✅ Leaderboard loaded?
- 📈 Количество записей
- ⏰ Время выполнения

---

## ❓ Если что-то не работает

### Проблема: Scheduler не запускается

```bash
# Проверить статус контейнера
docker-compose ps

# Если не запущен
docker-compose up -d scheduler

# Проверить логи
docker logs polystars_scheduler
```

### Проблема: Данные не загружаются

```bash
# Проверить подключение к БД
docker exec polystars_scheduler python /app/scripts/db/test_db_connection.py

# Запустить вручную с выводом
docker exec -it polystars_scheduler python /app/scripts/daily_scheduler.py --run

# Проверить логи
docker exec polystars_scheduler tail -100 /app/logs/scheduler.log
```

### Проблема: Cron не работает

```bash
# Проверить cron процесс
docker exec polystars_scheduler ps aux | grep cron

# Проверить crontab
docker exec polystars_scheduler crontab -l

# Пересоздать scheduler
docker-compose stop scheduler
docker-compose rm -f scheduler
docker-compose build scheduler
docker-compose up -d scheduler
```

---

## 📚 Дополнительная информация

- **Полная документация:** [SCHEDULER_SETUP.md](./SCHEDULER_SETUP.md)
- **Docker документация:** [DOCKER.md](./DOCKER.md)
- **Настройка БД:** [docs/DATABASE_SETUP.md](./docs/DATABASE_SETUP.md)

---

**Версия:** 1.0  
**Дата:** 2026-02-09

**Готово! 🎉**

Теперь ваша система будет автоматически загружать данные каждый день!
