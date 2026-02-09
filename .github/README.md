# 📖 Документация PolyStars

## Начните здесь

### 🚀 [START.md](../START.md)
Быстрый старт за 2 минуты. Запуск Docker и загрузка данных.

### 🐳 [DOCKER.md](../DOCKER.md)
Полная документация по Docker. Все команды и настройки.

### 📘 [README.md](../README.md)
Основная документация проекта. Структура, установка, использование.

---

## Структура проекта

```
PolyStars/
├── START.md              ← Начните отсюда
├── DOCKER.md             ← Docker инструкции
├── README.md             ← Полная документация
│
├── docker-compose.yml    # Docker конфигурация
├── Dockerfile.python     # Python образ
├── .env                  # Переменные окружения
│
├── scripts/              # Python ETL скрипты
│   ├── fetch/            # Загрузка данных
│   ├── db/               # Работа с БД
│   └── analytics/        # Аналитика
│
├── sql/schemas/          # SQL схемы
│   └── init-db.sql       # Автоинициализация БД
│
├── app/                  # Next.js приложение
├── components/           # React компоненты
└── lib/                  # Утилиты
```

---

## Быстрая навигация

| Что нужно | Файл |
|-----------|------|
| Запустить проект | [START.md](../START.md) |
| Docker команды | [DOCKER.md](../DOCKER.md) |
| Структура проекта | [README.md](../README.md) |
| Настройка БД | [sql/schemas/](../sql/schemas/) |
| Python скрипты | [scripts/](../scripts/) |

---

**Версия:** 1.0  
**Дата:** 2026-02-08
