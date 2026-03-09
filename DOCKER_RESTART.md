URL	Что это
http://localhost:8088	web стек (Next.js + FastAPI через nginx)
http://localhost:8089	user web стек (то же, второй стек)
http://localhost:5050	pgAdmin (UI для PostgreSQL)

# Перезапуск Docker и работа с внешней БД

## 🔄 Быстрый перезапуск

### Единый запуск всех сервисов (core + web):
```bash
docker compose up -d --build
```

### Остановить все сервисы:
```bash
docker compose down
```

### Перезапустить только веб-часть:
```bash
docker compose restart web_backend web_frontend web_nginx
```

### Пересобрать только веб-часть:
```bash
docker compose up -d --build web_backend web_frontend web_nginx
```

### Проверить веб:
- Приложение: `http://localhost:8088`
- API health: `http://localhost:8088/api/health`

## 🚀 Production (VPS)

### 1) Подготовить production env
- Используй файл `.env.prod` (не храни секреты в git).
- Проверь обязательные переменные:
  - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE=require`
  - `DATABASE_URL`
  - `SESSION_SECRET`
  - `PINATA_JWT`
  - `ZORA_MINTER_PRIVATE_KEY`

### 2) Запуск на сервере
```bash
docker compose --env-file .env.prod up -d --build
```

### 3) Проверка после запуска
```bash
docker compose --env-file .env.prod ps
curl -sS http://localhost:8088/api/health
curl -sS http://localhost:8088/api/server-time
```

### 4) Обновление (deploy новой версии)
```bash
git pull
docker compose --env-file .env.prod up -d --build
```

### 5) Rollback (если нужно быстро откатить)
```bash
git checkout <previous-commit-or-tag>
docker compose --env-file .env.prod up -d --build
```

### Обновить .env и перезапустить:
```bash
docker compose restart
```

### Полный перезапуск (с пересборкой):
```bash
docker compose down
docker compose up -d
```

### Пересборка после изменения кода:
```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## 🗄️ Как работает внешняя БД

### Архитектура:
```
Docker Контейнеры          Внешняя PostgreSQL
┌─────────────────┐        ┌──────────────────┐
│  python_scripts │───────▶│                  │
│  scheduler      │───────▶│  Managed DB      │
│  pgadmin        │───────▶│  (DigitalOcean)  │
└─────────────────┘        └──────────────────┘
```

### Ключевые моменты:

**1. Данные хранятся ВНЕ Docker**
- Контейнеры - только код и логика
- БД - отдельный сервер (локальный или облачный)
- Можно удалять/пересобирать контейнеры без потери данных

**2. Подключение через .env**
```env
DB_HOST=your-db-host.com    # Адрес БД
DB_PORT=25060               # Порт
DB_USER=doadmin             # Пользователь
DB_PASSWORD=***             # Пароль
DB_SSLMODE=require          # SSL обязателен
```

**3. Переключение между окружениями**
```bash
# DEV (локальная/тестовая среда):
docker compose --env-file .env up -d --build

# PROD (VPS + managed DB):
docker compose --env-file .env.prod up -d --build
```

## 📊 Проверка подключения

```bash
# Проверить подключение к БД:
docker compose exec python_scripts python scripts/db/test_db_connection.py

# Посмотреть логи:
docker compose logs python_scripts
docker compose logs scheduler

# ⭐ НОВОЕ! Посмотреть логи Python скриптов в реальном времени:
# Все логи из fetch_redemptions.py, fetch_events_parallel_optimized.py, 
# fetch_trader_leaderboard_parallel.py, fetch_user_closed_positions_parallel.py
# теперь видны через docker logs!
docker logs -f polystars_scheduler

# Посмотреть последние 100 строк логов с live-обновлением:
docker compose logs -f --tail=100 scheduler

# Статус контейнеров:
docker compose ps
```

## 🔄 Catch-up с автоматической перепроверкой

### Умная система catch-up
Если `--catch-up` работает >24 часа, за это время может пройти новый день. 
Система **автоматически обнаруживает** это и запускает повторную итерацию!

**Защита:**
- Максимум 10 итераций (защита от бесконечного цикла)
- Один lock на все итерации (блокирует concurrent runs)
- Полная статистика по всем итерациям

**Пример работы:**
```bash
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up

# Итерация 1: Обработано 50 дней (заняло 25 часов)
# ✅ Загружено 50 дней

# 🔍 Проверка новых пропусков...
# ⚠️  Обнаружен новый пропуск: 1 день (за время работы прошёл новый день!)

# Итерация 2: Обработан 1 день
# ✅ Загружен 1 день

# 🔍 Проверка новых пропусков...
# ✅ Новых пропусков нет - catch-up завершён!

# 🎉 ФИНАЛЬНАЯ СТАТИСТИКА:
#    ✅ Всего загружено: 51 день
#    🔄 Итераций: 2
#    ⏱️  Общее время: 25.5 часов
```

## 🧹 Управление логами

### 📺 Логи через Docker (Real-time)
**НОВОЕ!** Все логи Python скриптов теперь дублируются в stdout контейнера:

```bash
# Смотреть логи scheduler'а в реальном времени:
docker logs -f polystars_scheduler

# Посмотреть последние 100 строк:
docker logs --tail 100 polystars_scheduler

# Логи с временными метками:
docker logs --timestamps polystars_scheduler
```

**Что видно:**
- ✅ Логи из `fetch_redemptions.py` (redemptions fetcher)
- ✅ Логи из `fetch_events_parallel_optimized.py` (events fetcher)
- ✅ Логи из `fetch_trader_leaderboard_parallel.py` (leaderboard fetcher)
- ✅ Логи из `fetch_user_closed_positions_parallel.py` (positions fetcher)
- ✅ Все print() выводы и ошибки
- ✅ Логи cron jobs (daily pipeline, cleanup)

**Как это работает:**
- Логи записываются **одновременно** в файлы (/app/logs/) **И** в stdout
- Используется команда `tee` для дублирования потока
- Python запускается с `PYTHONUNBUFFERED=1` для отключения буферизации
- Файловые логи сохраняются для истории, Docker логи - для мониторинга
- Обновление сезонности запускается cron в `00:00 UTC` (`--season-update`)
- Daily pipeline запускается cron в `02:00 UTC` (`--run`)

### Автоматическая очистка
Логи автоматически очищаются каждое воскресенье в 3:00 AM UTC:
- Хранятся последние 14 дней
- Старые файлы удаляются автоматически

### Ручная очистка логов
```bash
# Посмотреть что будет удалено (dry run):
docker exec polystars_scheduler python /app/scripts/utils/cleanup_old_logs.py --dry-run

# Удалить логи старше 14 дней:
docker exec polystars_scheduler python /app/scripts/utils/cleanup_old_logs.py

# Удалить логи старше 7 дней:
docker exec polystars_scheduler python /app/scripts/utils/cleanup_old_logs.py --keep-days 7
```

### Ограничения размера Docker логов
Docker автоматически ротирует логи:
- Максимум 10MB на файл
- 3 ротируемых файла (всего 30MB)
- Старые логи автоматически сжимаются

```bash
# Проверить размер Docker логов:
docker inspect polystars_scheduler --format='{{.HostConfig.LogConfig}}'

# Очистить все Docker логи:
docker compose down
rm -rf /var/lib/docker/containers/*/*-json.log
docker compose up -d
```

## 💡 Когда перезапускать

| Изменение | Команда |
|-----------|---------|
| Только .env | `docker compose restart` |
| Python код (scripts/) | `docker compose restart` |
| Dockerfile | `docker compose build --no-cache && docker compose up -d` |
| docker-compose.yml | `docker compose down && docker compose up -d` |

## 🎯 Важно

- **Данные не теряются** при перезапуске - они в БД
- **Volumes не нужны** для данных (только для pgadmin_data)
- **SSL всегда включен** для Managed DB
- **host.docker.internal** для локальной БД на Windows


docker compose up -d --build user_web_backend user_web_frontend user_web_nginx
curl -sS http://127.0.0.1:8089/api/health
curl -sS -X POST http://127.0.0.1:8089/api/auth/wallet/challenge -H "Content-Type: application/json" -d '{"wallet_address":"0x0000000000000000000000000000000000000001"}'

Без Docker (локально backend + frontend)
Терминал 1:

cd /Users/agmac/Desktop/PolyStars
ENV_FILE=.env uvicorn user_web_backend.main:app --host 0.0.0.0 --port 8011 --reload
Терминал 2:

cd /Users/agmac/Desktop/PolyStars/user_web_frontend
npm install
NEXT_PUBLIC_USER_API_BASE_URL=http://127.0.0.1:8011 npm run dev
Проверка:

curl -sS http://127.0.0.1:8011/api/health

