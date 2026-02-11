# Перезапуск Docker и работа с внешней БД

## 🔄 Быстрый перезапуск

### Обновить .env и перезапустить:
```bash
docker-compose restart
```

### Полный перезапуск (с пересборкой):
```bash
docker-compose down
docker-compose up -d
```

### Пересборка после изменения кода:
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
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

**3. Переключение между БД**
```bash
# Локальная БД для тестирования:
cp .env.local .env
docker-compose restart

# Managed DB для production:
cp .env.managed .env
docker-compose restart
```

## 📊 Проверка подключения

```bash
# Проверить подключение к БД:
docker-compose exec python_scripts python scripts/db/test_db_connection.py

# Посмотреть логи:
docker-compose logs python_scripts
docker-compose logs scheduler

# Статус контейнеров:
docker-compose ps
```

## 💡 Когда перезапускать

| Изменение | Команда |
|-----------|---------|
| Только .env | `docker-compose restart` |
| Python код (scripts/) | `docker-compose restart` |
| Dockerfile | `docker-compose build --no-cache && docker-compose up -d` |
| docker-compose.yml | `docker-compose down && docker-compose up -d` |

## 🎯 Важно

- **Данные не теряются** при перезапуске - они в БД
- **Volumes не нужны** для данных (только для pgadmin_data)
- **SSL всегда включен** для Managed DB
- **host.docker.internal** для локальной БД на Windows
