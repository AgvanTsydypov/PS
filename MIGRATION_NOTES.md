# Заметки о миграции проекта

## ✅ Что было сделано

### 1. Переструктурирован проект
- ✅ Next.js приложение перемещено в корень
- ✅ Python скрипты организованы в `scripts/` с подпапками
- ✅ SQL файлы организованы в `sql/queries` и `sql/schemas`
- ✅ Данные перемещены в `data/`
- ✅ Создана папка `docs/` для документации

### 2. Новая структура

```
PolyStars/
├── app/                    # Next.js приложение
├── components/             # React компоненты
├── hooks/                  # React хуки
├── lib/                    # Утилиты (Polymarket API, session, wagmi)
├── types/                  # TypeScript типы
│
├── scripts/
│   ├── fetch/              # Загрузка данных
│   ├── analytics/          # Анализ данных
│   ├── db/                 # Работа с БД
│   ├── utils/              # Утилиты
│   └── node/               # Node.js скрипты
│
├── sql/
│   ├── queries/            # SQL запросы
│   └── schemas/            # SQL схемы
│
├── data/
│   ├── output/             # Результаты
│   ├── json_output/        # JSON данные
│   └── logs/               # Логи
│
├── docs/                   # Документация
└── public/                 # Статические файлы
```

### 3. Файлы конфигурации
- ✅ `package.json` перемещен в корень
- ✅ `tsconfig.json`, `next.config.js`, `tailwind.config.js` в корне
- ✅ `.gitignore` обновлен для Python + Node.js
- ✅ `.env.example` перемещен в корень
- ✅ Новый `README.md` с описанием структуры

## 📝 Следующие шаги

### 1. Удалите старую папку вручную
Папка `polymarket-siwe-auth` не может быть удалена автоматически, так как используется процессом.

**Закройте все процессы** (Node.js сервер, VS Code, File Explorer), затем:
```powershell
Remove-Item -Path ".\polymarket-siwe-auth" -Recurse -Force
```

Или просто удалите папку через проводник Windows.

### 2. Переустановите Node.js зависимости
```bash
# Удалите старый node_modules если нужно
rm -rf node_modules

# Установите зависимости
npm install
```

### 3. Обновите пути в скриптах (если нужно)

#### Python скрипты
Проверьте пути к данным в скриптах. Некоторые могут использовать:
- ❌ Старый путь: `./output/...` 
- ✅ Новый путь: `./data/output/...`

#### Примеры файлов для проверки:
- `scripts/fetch/fetch_events_parallel_optimized.py`
- `scripts/analytics/get_trades.py`
- `scripts/db/supabase_uploader.py`

### 4. Обновите импорты (если есть)
Если Python скрипты импортируют друг друга:
```python
# ❌ Старый импорт
from polymarket_client import PolymarketClient

# ✅ Новый импорт
from scripts.db.polymarket_client import PolymarketClient
```

### 5. Flask API (app.py)
Файл `app.py` в корне - это Flask REST API для Polymarket данных.

✅ **Импорт уже обновлен:**
```python
from scripts.db.polymarket_client import PolymarketClient
```

**Запуск Flask API:**
```bash
# Активируйте venv
venv\Scripts\activate

# Запустите Flask
python app.py
```

Сервер запустится на `http://localhost:5000`

📚 Подробнее: [docs/API.md](docs/API.md)

### 6. Запустите приложение
```bash
# Next.js
npm run dev

# Python скрипты (активируйте venv)
venv\Scripts\activate
python scripts/analytics/get_trades.py
```

## ⚠️ Важные замечания

1. **node_modules** может занимать много места - пересоздайте его:
   ```bash
   rm -rf node_modules
   npm install
   ```

2. **Python virtual environment** остался в `venv/` - переустановки не требуется

3. **База данных** - все SQL скрипты и схемы в `sql/`

4. **Документация** - основная документация в `docs/`, README в корне

## 🐛 Возможные проблемы

### Проблема: "Module not found"
**Решение**: Обновите импорты в Python скриптах или добавьте корень в PYTHONPATH

### Проблема: "Cannot find data files"
**Решение**: Обновите пути в скриптах с `./output/` на `./data/output/`

### Проблема: Next.js не запускается
**Решение**: 
```bash
rm -rf node_modules .next
npm install
npm run dev
```

## ✨ Преимущества новой структуры

1. ✅ **Единый корень** - Next.js приложение в корне проекта
2. ✅ **Организованные скрипты** - легко найти нужный скрипт
3. ✅ **Разделение данных** - все данные в одной папке `data/`
4. ✅ **Чистая структура** - логическое разделение по типам файлов
5. ✅ **Единые зависимости** - один `package.json` для всего проекта
6. ✅ **Документация** - вся документация в `docs/`

## 📚 Дополнительная информация

- [README.md](README.md) - общее описание проекта
- [docs/START_HERE.md](docs/START_HERE.md) - быстрый старт
- [docs/SETUP.md](docs/SETUP.md) - детальная настройка
- [docs/DATABASE_SETUP.md](docs/DATABASE_SETUP.md) - настройка БД
