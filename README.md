# PolyStars - Polymarket Analytics & SIWE Authentication

Полнофункциональное приложение для анализа данных Polymarket с защищенной аутентификацией через SIWE (Sign-In With Ethereum).

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
