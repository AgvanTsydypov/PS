# Local run (без Docker)

Все команды запускать из корня репозитория `/Users/agmac/Desktop/PolyStars`.

## Backends

```bash
# web_backend — порт 8001
uvicorn web_backend.main:app --host 0.0.0.0 --port 8001 --reload

# user_web_backend — порт 8011
uvicorn user_web_backend.main:app --host 0.0.0.0 --port 8011 --reload
```

## Frontends

Первый запуск — поставить зависимости (каждую команду отдельно из корня проекта):
```bash
cd /Users/agmac/Desktop/PolyStars/web_frontend && npm install
cd /Users/agmac/Desktop/PolyStars/user_web_frontend && npm install
```

```bash
# web_frontend — порт 3000
cd web_frontend && npm run dev

# user_web_frontend — порт 3001
cd user_web_frontend && npm run dev -- -p 3001
```

## Итого

| Сервис            | URL                        |
|-------------------|----------------------------|
| web_backend       | http://localhost:8001      |
| user_web_backend  | http://localhost:8011      |
| web_frontend      | http://localhost:3000      |
| user_web_frontend | http://localhost:3001      |
