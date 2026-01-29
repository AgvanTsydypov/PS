# Flask API Documentation

## Обзор

`app.py` в корне проекта - это Flask API сервер, который предоставляет REST API для работы с данными Polymarket.

## Запуск

```bash
# Активируйте virtual environment
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Запустите Flask сервер
python app.py
```

Сервер запустится на `http://localhost:5000`

## Endpoints

### 🏠 Home
- **GET** `/`
- Отображает HTML страницу с информацией об API

### 📊 Markets

#### Получить список рынков
- **GET** `/api/markets`
- **Query Parameters:**
  - `limit` (int, default: 10) - количество результатов
  - `offset` (int, default: 0) - смещение для пагинации
  - `closed` (string: "true"/"false") - фильтр по закрытым рынкам
  - `tag_id` (string) - фильтр по тегу
  - `order` (string, default: "id") - поле для сортировки
  - `ascending` (string: "true"/"false", default: "false") - порядок сортировки

**Пример:**
```bash
curl "http://localhost:5000/api/markets?limit=5&closed=false"
```

#### Получить рынок по slug
- **GET** `/api/markets/slug/<slug>`
- **URL Parameters:**
  - `slug` (string) - уникальный slug рынка

**Пример:**
```bash
curl "http://localhost:5000/api/markets/slug/will-trump-win-2024"
```

### 📅 Events

#### Получить список событий
- **GET** `/api/events`
- **Query Parameters:** (такие же как у `/api/markets`)

**Пример:**
```bash
curl "http://localhost:5000/api/events?limit=10&order=volume"
```

#### Получить событие по slug
- **GET** `/api/events/slug/<slug>`
- **URL Parameters:**
  - `slug` (string) - уникальный slug события

**Пример:**
```bash
curl "http://localhost:5000/api/events/slug/presidential-election-2024"
```

### 🏷️ Tags

#### Получить все теги
- **GET** `/api/tags`
- Возвращает список всех доступных тегов

**Пример:**
```bash
curl "http://localhost:5000/api/tags"
```

### ⚽ Sports

#### Получить спортивные теги
- **GET** `/api/sports`
- Возвращает все спортивные теги и метаданные

**Пример:**
```bash
curl "http://localhost:5000/api/sports"
```

## Response Format

### Success Response
```json
{
  "data": [...],
  "meta": {
    "limit": 10,
    "offset": 0
  }
}
```

### Error Response
```json
{
  "error": "Error message description"
}
```

## Использование с Next.js

Flask API можно использовать как backend для Next.js приложения:

```typescript
// lib/api-client.ts
const FLASK_API_URL = process.env.NEXT_PUBLIC_FLASK_API_URL || 'http://localhost:5000';

export async function getMarkets(params?: {
  limit?: number;
  offset?: number;
  closed?: boolean;
}) {
  const queryParams = new URLSearchParams();
  if (params?.limit) queryParams.set('limit', params.limit.toString());
  if (params?.offset) queryParams.set('offset', params.offset.toString());
  if (params?.closed !== undefined) queryParams.set('closed', params.closed.toString());

  const response = await fetch(`${FLASK_API_URL}/api/markets?${queryParams}`);
  return response.json();
}
```

## Production

Для production окружения используйте gunicorn или другой WSGI сервер:

```bash
# Установите gunicorn
pip install gunicorn

# Запустите с gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Примечания

- Flask API работает независимо от Next.js приложения
- Можно использовать оба одновременно:
  - Next.js на порту 3000 (frontend)
  - Flask на порту 5000 (backend API)
- Templates для Flask находятся в папке `templates/`
