# ⚡ Быстрые команды (шпаргалка)

## 🔍 Просмотр данных

### Количество записей:
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "SELECT 'events' as table_name, COUNT(*) as records FROM events UNION ALL SELECT 'markets', COUNT(*) FROM markets UNION ALL SELECT 'redemptions', COUNT(*) FROM redemptions UNION ALL SELECT 'user_positions', COUNT(*) FROM user_positions UNION ALL SELECT 'trader_leaderboard', COUNT(*) FROM trader_leaderboard;"
```

### Статус загрузок:
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "SELECT load_date, events_loaded, redemptions_loaded, positions_loaded, leaderboard_loaded, load_type, events_count FROM data_loads ORDER BY load_date DESC LIMIT 10;"
```

### Пропущенные дни:
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --check
```

---

## 🔄 Загрузка данных

### Genesis (первый раз):
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical
```

### Пропущенные дни:
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up
```

### Ежедневная загрузка:
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run
```

### Dry-run (без загрузки):
```powershell
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical --dry-run
docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --catch-up --dry-run
```

---

## 🗑️ Очистка

### Только tracking (данные останутся):
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "TRUNCATE data_loads;"
```

### Полная очистка:
```powershell
docker exec polystars_postgres psql -U postgres -d polymarket -c "TRUNCATE events, markets, redemptions, user_closed_positions, trader_leaderboard, data_loads CASCADE;"
```

---

## 🐳 Docker

### Перезапуск контейнеров:
```powershell
docker-compose restart
```

### Пересборка после изменений:
```powershell
docker-compose up -d --build
```

### Логи:
```powershell
docker logs -f polystars_scheduler
docker logs -f polystars_postgres
```

### Остановить все:
```powershell
docker-compose down
```

### Остановить + удалить данные:
```powershell
docker-compose down -v
```

---

## 📊 SQL консоль

### Открыть:
```powershell
docker exec -it polystars_postgres psql -U postgres -d polymarket
```

### Полезные команды внутри psql:
```sql
-- Список таблиц
\dt

-- Структура таблицы
\d events

-- Выход
\q
```
