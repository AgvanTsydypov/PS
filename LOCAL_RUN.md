# Local run (без Docker)

Все команды запускать из корня репозитория `/Users/agmac/Desktop/PolyStars`.

## База (порядок SQL)

Сначала `sql/schemas/init-db.sql`, затем `sql/schemas/create_seasons_system.sql`. Иначе бэкфилл сезонов обращается к `participants` без нужных колонок (например `rarity_bracket`).

## Backends

```bash
# admin_backend — порт 8001
.\venv\Scripts\Activate.ps1 uvicorn admin_backend.main:app --host 0.0.0.0 --port 8001 --reload

# user_web_backend — порт 8011
.\venv\Scripts\Activate.ps1 uvicorn user_web_backend.main:app --host 0.0.0.0 --port 8011 --reload
```

## Frontends

Первый запуск — поставить зависимости (каждую команду отдельно из корня проекта):
```bash
cd /Users/agmac/Desktop/PolyStars/admin_frontend && npm install
cd /Users/agmac/Desktop/PolyStars/user_web_frontend && npm install
```

```bash
# admin_frontend — порт 3000
cd admin_frontend && npm run dev

# user_web_frontend — порт 3001
cd user_web_frontend && npm run dev -- -p 3001
```

## Итого

| Сервис            | URL                        |
|-------------------|----------------------------|
| admin_backend     | http://localhost:8001      |
| user_web_backend  | http://localhost:8011      |
| admin_frontend    | http://localhost:3000      |
| user_web_frontend | http://localhost:3001      |