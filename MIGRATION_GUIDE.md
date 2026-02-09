# 🔄 Руководство по миграции на систему сезонов

## Обзор изменений

Ваш проект PolyStars теперь имеет:
- ✅ **Автоматическую загрузку данных** 1 раз в сутки
- ✅ **Систему сезонов** (genesis, season1, season2, ...)
- ✅ **Отслеживание прогресса** загрузки
- ✅ **Историческую загрузку** при первом запуске
- ✅ **Проверку текущего сезона** и дня
- ✅ **Автоматическое восстановление** пропущенных дней

---

## 📋 Что было добавлено

### Новые файлы

```
scripts/
├── season_manager.py                      # Менеджер сезонов
├── daily_scheduler.py                     # Главный планировщик
└── cron/
    ├── setup_cron.sh                     # Настройка cron
    └── docker-entrypoint-scheduler.sh    # Entrypoint для scheduler

sql/schemas/
└── add_season_tables.sql                 # SQL миграция для таблиц сезонов

Dockerfile.scheduler                       # Docker образ для scheduler
SCHEDULER_SETUP.md                        # Полная документация
QUICK_START_SCHEDULER.md                  # Быстрый старт
MIGRATION_GUIDE.md                        # Этот файл
```

### Обновленные файлы

- `docker-compose.yml` - добавлен сервис `scheduler`
- `DOCKER.md` - добавлена документация по scheduler

### Новые таблицы в БД

- `seasons` - Метаданные о сезонах
- `season_data_loads` - Отслеживание ежедневных загрузок

---

## 🚀 Как применить изменения

### Шаг 1: Обновить код (если клонируете из git)

```bash
cd /path/to/PolyStars
git pull origin main
```

### Шаг 2: Применить миграцию БД

**Вариант А: Автоматически (при запуске scheduler)**

Scheduler автоматически создаст таблицы при первом запуске.

```bash
# Просто запустите
docker-compose up -d
```

**Вариант Б: Вручную**

```bash
# Если Docker уже запущен
docker exec -i polystars_postgres psql -U postgres -d polymarket < sql/schemas/add_season_tables.sql

# Или подключитесь и выполните
docker exec -it polystars_postgres psql -U postgres -d polymarket
\i /docker-entrypoint-initdb.d/add_season_tables.sql
```

**Вариант В: Через psql локально**

```bash
psql -U postgres -d polymarket -f sql/schemas/add_season_tables.sql
```

### Шаг 3: Пересобрать Docker образы

```bash
# Остановить текущие контейнеры
docker-compose down

# Пересобрать образы
docker-compose build

# Запустить все сервисы
docker-compose up -d
```

### Шаг 4: Проверить что всё работает

```bash
# Проверить статус контейнеров
docker-compose ps

# Проверить статус сезона
docker exec polystars_scheduler python /app/scripts/season_manager.py --status

# Проверить что cron работает
docker exec polystars_scheduler crontab -l
```

---

## 🔍 Проверка миграции

### Проверить таблицы

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Проверить что таблицы созданы
\dt seasons
\dt season_data_loads

-- Проверить views
\dv current_season_status
\dv daily_load_status

-- Посмотреть структуру
\d seasons
\d season_data_loads
```

### Проверить scheduler

```bash
# Статус контейнера
docker ps | grep polystars_scheduler

# Логи scheduler
docker logs polystars_scheduler

# Проверить cron
docker exec polystars_scheduler ps aux | grep cron

# Проверить crontab
docker exec polystars_scheduler crontab -l
```

### Тестовый запуск

```bash
# Тест без выполнения (dry-run)
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --dry-run

# Реальный запуск
docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

---

## 📊 Проверка данных после первой загрузки

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Статус сезонов
SELECT * FROM current_season_status;

-- Последние загрузки
SELECT * FROM daily_load_status LIMIT 10;

-- Детальная информация
SELECT 
    season_name,
    load_date,
    day_in_season,
    events_loaded,
    redemptions_loaded,
    positions_loaded,
    leaderboard_loaded,
    events_count,
    redemptions_count,
    positions_count,
    leaderboard_count
FROM season_data_loads
ORDER BY load_date DESC
LIMIT 10;
```

---

## ⚠️ Возможные проблемы и решения

### Проблема: Таблицы уже существуют

**Ошибка:**
```
ERROR: relation "seasons" already exists
```

**Решение:**
Это нормально! SQL скрипт использует `CREATE TABLE IF NOT EXISTS`, поэтому повторное выполнение безопасно.

### Проблема: Scheduler не может подключиться к БД

**Ошибка:**
```
could not connect to server: Connection refused
```

**Решение:**
```bash
# Проверить что PostgreSQL запущен
docker-compose ps postgres

# Если не запущен
docker-compose up -d postgres

# Подождать пока БД будет готова
docker exec polystars_scheduler pg_isready -h postgres -U postgres

# Перезапустить scheduler
docker-compose restart scheduler
```

### Проблема: Cron не настроен

**Ошибка:**
```
no crontab for root
```

**Решение:**
```bash
# Зайти в контейнер
docker exec -it polystars_scheduler bash

# Настроить cron
/app/scripts/cron/setup_cron.sh

# Проверить
crontab -l

# Выйти
exit
```

### Проблема: Скрипты не выполняются

**Решение:**
```bash
# Проверить права на файлы
docker exec polystars_scheduler ls -la /app/scripts/

# Проверить Python путь
docker exec polystars_scheduler which python

# Проверить переменные окружения
docker exec polystars_scheduler env | grep DB

# Тест подключения к БД
docker exec polystars_scheduler python /app/scripts/db/test_db_connection.py

# Запустить вручную с подробным выводом
docker exec -it polystars_scheduler python /app/scripts/daily_scheduler.py --run
```

---

## 🔄 Откат изменений (если нужно)

### Удалить scheduler

```bash
# Остановить scheduler
docker-compose stop scheduler

# Удалить контейнер
docker-compose rm -f scheduler

# Удалить образ
docker rmi polystars-scheduler

# Запустить без scheduler
docker-compose up -d postgres python_scripts
```

### Удалить таблицы сезонов

```sql
-- Подключиться к БД
docker exec -it polystars_postgres psql -U postgres -d polymarket

-- Удалить views
DROP VIEW IF EXISTS current_season_status;
DROP VIEW IF EXISTS daily_load_status;

-- Удалить таблицы
DROP TABLE IF EXISTS season_data_loads;
DROP TABLE IF EXISTS seasons;
```

### Вернуть старый docker-compose.yml

```bash
# Через git (если использовали)
git checkout docker-compose.yml

# Или вручную удалите секцию scheduler из docker-compose.yml
```

---

## ✅ Checklist миграции

- [ ] Обновлен код (git pull или скопированы файлы)
- [ ] Применена SQL миграция (таблицы созданы)
- [ ] Docker образы пересобраны (docker-compose build)
- [ ] Все сервисы запущены (docker-compose up -d)
- [ ] Проверен статус scheduler (docker-compose ps)
- [ ] Проверены таблицы в БД (\dt seasons, \dt season_data_loads)
- [ ] Проверен cron (docker exec polystars_scheduler crontab -l)
- [ ] Выполнен тестовый запуск (--dry-run)
- [ ] Проверены логи (docker logs polystars_scheduler)
- [ ] Прочитана документация (SCHEDULER_SETUP.md, QUICK_START_SCHEDULER.md)

---

## 📚 Следующие шаги

1. **Прочитайте документацию:**
   - [QUICK_START_SCHEDULER.md](./QUICK_START_SCHEDULER.md) - Быстрый старт
   - [SCHEDULER_SETUP.md](./SCHEDULER_SETUP.md) - Полная документация

2. **Настройте расписание:**
   - По умолчанию: каждый день в 2:00 UTC
   - Можно изменить в `scripts/cron/setup_cron.sh`

3. **Мониторьте прогресс:**
   - `season_manager.py --status` - текущий статус
   - SQL запросы к таблицам `season_data_loads`
   - Логи в `/app/logs/scheduler.log`

4. **Настройте оповещения (опционально):**
   - Email при ошибках
   - Slack/Discord уведомления
   - Мониторинг здоровья контейнера

---

## 🆘 Поддержка

Если возникли проблемы:

1. **Проверьте логи:**
   ```bash
   docker logs polystars_scheduler
   docker exec polystars_scheduler tail -100 /app/logs/scheduler.log
   ```

2. **Проверьте статус:**
   ```bash
   docker exec polystars_scheduler python /app/scripts/season_manager.py --status
   ```

3. **Запустите тестовый режим:**
   ```bash
   docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run --dry-run
   ```

4. **Проверьте документацию:**
   - [SCHEDULER_SETUP.md](./SCHEDULER_SETUP.md) - раздел "Устранение неполадок"

---

**Версия:** 1.0  
**Дата:** 2026-02-09  

**Удачной миграции! 🚀**
