# PolyStars - Polymarket Analytics & SIWE Authentication

Полнофункциональное приложение для анализа данных Polymarket с защищенной аутентификацией через SIWE (Sign-In With Ethereum).

## 📚 Документация

- **[START.md](START.md)** - быстрый старт (2 минуты) 🚀
- **[DOCKER.md](DOCKER.md)** - Docker документация
- **[README.md](README.md)** - полная документация (этот файл)

## 🏗 Структура проекта

```
PolyStars/
├── app/                    # Next.js приложение (App Router)
├── components/             # React компоненты
├── hooks/                  # React хуки
├── lib/                    # Утилиты и конфиги (Polymarket API, session, wagmi)
├── types/                  # TypeScript типы
├── middleware.ts           # Next.js middleware для защиты маршрутов
│
├── scripts/                # Python & Node.js скрипты
│   ├── fetch/              # Скрипты загрузки данных из Polymarket
│   ├── analytics/          # Аналитические скрипты
│   ├── db/                 # Работа с базой данных
│   ├── utils/              # Утилиты
│   └── node/               # Node.js скрипты (генерация секретов)
│
├── sql/                    # SQL файлы
│   ├── queries/            # SQL запросы для анализа
│   └── schemas/            # Схемы таблиц БД
│
├── data/                   # Данные проекта
│   ├── output/             # Результаты обработки
│   ├── json_output/        # JSON данные
│   └── logs/               # Логи выполнения скриптов
│
├── docs/                   # Документация
│   ├── START_HERE.md       # Быстрый старт
│   ├── SETUP.md            # Детальная настройка
│   └── DATABASE_SETUP.md   # Настройка БД
│
├── public/                 # Статические файлы Next.js
├── templates/              # Шаблоны (если нужны)
│
├── package.json            # Node.js зависимости
├── requirements.txt        # Python зависимости
├── next.config.js          # Конфигурация Next.js
├── tsconfig.json           # Конфигурация TypeScript
└── .env.example            # Пример переменных окружения
```

## 🚀 Быстрый старт

### Docker (рекомендуется) 🐳

```bash
# 1. Запустить контейнеры
docker-compose up -d

# 2. Проверить подключение
docker exec polystars_python python scripts/db/test_db_connection.py

# 3. Загрузить данные
docker exec polystars_python python scripts/fetch/fetch_events_parallel_optimized.py
```

📖 **Подробнее:** [DOCKER.md](DOCKER.md)

### Локальная установка

### 1. Установка зависимостей

#### Node.js (для веб-приложения)
```bash
npm install
```

#### Python (для скриптов анализа)
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 2. Настройка переменных окружения

Скопируйте `.env.example` в `.env.local`:
```bash
cp .env.example .env.local
```

Заполните переменные:
```env
# Секретный ключ для iron-session (минимум 32 символа)
SESSION_SECRET=your-super-secret-key-at-least-32-characters-long

# URL приложения
NEXT_PUBLIC_APP_URL=http://localhost:3000

# WalletConnect Project ID
NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=your-walletconnect-project-id

# Database URL
DATABASE_URL=postgresql://postgres:password@localhost:5432/polymarket_nft
```

### 3. Запуск веб-приложения

```bash
npm run dev
```

Откройте [http://localhost:3000](http://localhost:3000)

## 📊 Скрипты анализа данных

### Загрузка данных
```bash
# Загрузка событий Polymarket
python scripts/fetch/fetch_events_parallel_optimized.py

# Загрузка redemptions
python scripts/fetch/fetch_redemptions.py

# Загрузка закрытых позиций пользователей
python scripts/fetch/fetch_user_closed_positions_parallel.py
```

### Аналитика
```bash
# Получение трейдов
python scripts/analytics/get_trades.py

# Подсчет рынков
python scripts/analytics/count_markets.py

# Просмотр логов
python scripts/analytics/view_logs.py
```

### Работа с БД
```bash
# Загрузка данных в Supabase
python scripts/db/supabase_uploader.py

# Тест подключения к БД
python scripts/db/test_db_connection.py
```

## 🔒 Аутентификация SIWE

Приложение использует Sign-In With Ethereum (SIWE) для безопасной аутентификации:

1. Пользователь подключает кошелек через ConnectKit
2. Автоматически запрашивается nonce с сервера
3. Формируется и подписывается SIWE сообщение
4. Сервер верифицирует подпись
5. Проверяется proxy wallet через Polymarket API
6. Сессия сохраняется в зашифрованных cookies

## 🛠 Технологии

### Frontend
- **Next.js 14** (App Router)
- **TypeScript**
- **Viem & Wagmi** - Ethereum интеграция
- **ConnectKit** - UI для кошельков
- **Tailwind CSS** - стилизация

### Backend
- **Python 3.12** - скрипты анализа
- **PostgreSQL / Supabase** - хранение данных
- **Iron-session** - защищенные сессии
- **SIWE** - аутентификация через Ethereum

## 📚 Документация

- [📖 Быстрый старт](docs/START_HERE.md) - первые шаги
- [⚙️ Настройка](docs/SETUP.md) - детальная конфигурация
- [🗄️ База данных](docs/DATABASE_SETUP.md) - настройка БД

## 🔧 Полезные команды

### Node.js скрипты
```bash
# Генерация секретного ключа для сессий
node scripts/node/generate-secret.js

# Запуск dev сервера
npm run dev

# Сборка для production
npm run build

# Запуск production сервера
npm start
```

### Python скрипты
```bash
# Активация виртуального окружения
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Установка новых зависимостей
pip install package-name
pip freeze > requirements.txt
```

## 📝 SQL запросы

SQL запросы находятся в `sql/queries/`:
- `all_100m_event_redeemers.sql` - все пользователи с redemption событий $100M+
- `avg_price_distribution.sql` - распределение средних цен
- `avg_price_others.sql` - средние цены для других категорий
- `lowest_100m_event_redeemers.sql` - пользователи с наименьшими redemption

## 🔐 Безопасность

- ✅ Все проверки выполняются на сервере
- ✅ SIWE защищает от replay-атак через nonce
- ✅ Iron-session шифрует cookies
- ✅ Нормализация адресов через viem
- ✅ Rate limiting на API эндпоинтах

## 📄 Лицензия

MIT - см. файл [LICENSE](LICENSE)

## 🤝 Разработка

Проект объединяет:
- **Веб-приложение**: аутентификация и интерфейс для пользователей
- **Аналитика**: Python скрипты для обработки данных Polymarket
- **База данных**: хранение и запросы к собранным данным

Все компоненты работают вместе для создания полнофункциональной платформы анализа Polymarket.


Команды для мониторинга (PowerShell):
# ✅ Historical/Genesis загрузка:
docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_historical_*.log 2>/dev/null | head -1)'

# ✅ Catch-up загрузка:
docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_catchup_*.log 2>/dev/null | head -1)'

# ✅ Daily pipeline:
docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_daily_*.log 2>/dev/null | head -1)'

# ✅ Redemptions fetch:
docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/redemptions_fetch_*.log 2>/dev/null | head -1)'

# ✅ Leaderboard fetch:
docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/leaderboard_fetch_*.log 2>/dev/null | head -1)'

# ✅ Positions fetch:
docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/positions_fetch_*.log 2>/dev/null | head -1)'

# 📝 Список ВСЕХ логов:
docker exec polystars_scheduler ls -lh /app/logs/*.log

# 🔍 Последние 50 строк из последнего лога (любой тип):
docker exec polystars_scheduler sh -c 'tail -50 $(ls -t /app/logs/*.log | head -1)'

🎯 Теперь ваши команды будут работать с логами:
# Запустить и мониторить:docker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historicaldocker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_historical_*.log | head -1)'# Запустить и мониторить catch-up:docker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-updocker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_catchup_*.log | head -1)'
Все логи теперь сохраняются в файлы и доступны для просмотра! 🎉


📊 Команды для мониторинга логов в Ubuntu/Linux:
🚀 Запуск скриптов:
# Historical/Genesis загрузкаdocker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical# Catch-up загрузкаdocker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up# Daily pipelinedocker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run# Fetch redemptionsdocker exec -d polystars_scheduler python /app/scripts/fetch/fetch_redemptions.py --upload --local# Fetch leaderboarddocker exec -d polystars_scheduler python /app/scripts/fetch/fetch_trader_leaderboard_parallel.py --upload --local --from-db# Fetch positionsdocker exec -d polystars_scheduler python /app/scripts/fetch/fetch_user_closed_positions_parallel.py --upload --local
👀 Мониторинг логов в реальном времени (tail -f):
# ✅ Historical/Genesis загрузка:docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_historical_*.log 2>/dev/null | head -1)'# ✅ Catch-up загрузка:docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_catchup_*.log 2>/dev/null | head -1)'# ✅ Daily pipeline:docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_daily_*.log 2>/dev/null | head -1)'# ✅ Redemptions fetch:docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/redemptions_fetch_*.log 2>/dev/null | head -1)'# ✅ Leaderboard fetch:docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/leaderboard_fetch_*.log 2>/dev/null | head -1)'# ✅ Positions fetch:docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/positions_fetch_*.log 2>/dev/null | head -1)'
📝 Просмотр последних N строк:
# Последние 50 строк из последнего historical лога:docker exec polystars_scheduler sh -c 'tail -50 $(ls -t /app/logs/scheduler_historical_*.log 2>/dev/null | head -1)'# Последние 100 строк из последнего redemptions лога:docker exec polystars_scheduler sh -c 'tail -100 $(ls -t /app/logs/redemptions_fetch_*.log 2>/dev/null | head -1)'# Последние 50 строк из ЛЮБОГО последнего лога:docker exec polystars_scheduler sh -c 'tail -50 $(ls -t /app/logs/*.log | head -1)'
📂 Список всех логов:
# Список ВСЕХ логов с размерами:docker exec polystars_scheduler ls -lh /app/logs/*.log# Список логов отсортированных по времени (последние сверху):docker exec polystars_scheduler ls -lt /app/logs/*.log# Только scheduler логи:docker exec polystars_scheduler ls -lt /app/logs/scheduler_*.log# Только redemptions логи:docker exec polystars_scheduler ls -lt /app/logs/redemptions_fetch_*.log
🔍 Поиск в логах:
# Найти ошибки в последнем логе:docker exec polystars_scheduler sh -c 'grep -i "error\|failed\|timeout" $(ls -t /app/logs/*.log | head -1)'# Найти все "✅ Completed" в redemptions логах:docker exec polystars_scheduler sh -c 'grep "✅ Completed" /app/logs/redemptions_fetch_*.log'# Показать последние 20 строк с ошибками:docker exec polystars_scheduler sh -c 'tail -1000 $(ls -t /app/logs/*.log | head -1) | grep -i error | tail -20'
🎯 Комбо команды (запуск + мониторинг):
# Запустить historical и сразу следить за логами:docker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical && \sleep 2 && \docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_historical_*.log | head -1)'# Запустить catch-up и сразу следить за логами:docker exec -d polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up && \sleep 2 && \docker exec polystars_scheduler sh -c 'tail -f $(ls -t /app/logs/scheduler_catchup_*.log | head -1)'
📊 Статистика и размеры:
# Общий размер всех логов:docker exec polystars_scheduler du -sh /app/logs/# Топ-5 самых больших логов:docker exec polystars_scheduler sh -c 'ls -lhS /app/logs/*.log | head -5'# Количество логов каждого типа:docker exec polystars_scheduler sh -c 'echo "Scheduler: $(ls /app/logs/scheduler_*.log 2>/dev/null | wc -l)" && \echo "Redemptions: $(ls /app/logs/redemptions_fetch_*.log 2>/dev/null | wc -l)" && \echo "Leaderboard: $(ls /app/logs/leaderboard_fetch_*.log 2>/dev/null | wc -l)" && \echo "Positions: $(ls /app/logs/positions_fetch_*.log 2>/dev/null | wc -l)"'
Все команды идентичны для Ubuntu и PowerShell, так как мы используем docker exec с sh -c внутри контейнера! 🎉