# 🗄️ Настройка базы данных

Инструкция по настройке PostgreSQL и Prisma для NFT Claim системы.

## 📋 Требования

- PostgreSQL 12+ (локально или в облаке)
- Node.js 18+
- npm или yarn

## 🚀 Быстрая настройка

### Вариант 1: Локальный PostgreSQL

#### 1. Установите PostgreSQL

**Windows:**
- Скачайте с [postgresql.org](https://www.postgresql.org/download/windows/)
- Установите с настройками по умолчанию
- Запомните пароль для пользователя `postgres`

**macOS (Homebrew):**
```bash
brew install postgresql@15
brew services start postgresql@15
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
```

#### 2. Создайте базу данных

```bash
# Подключитесь к PostgreSQL
psql -U postgres

# В psql консоли:
CREATE DATABASE polymarket_nft;

# Создайте пользователя (опционально)
CREATE USER polymarket_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE polymarket_nft TO polymarket_user;

# Выход
\q
```

#### 3. Настройте DATABASE_URL

Создайте `.env.local` и добавьте:

```env
DATABASE_URL="postgresql://postgres:your_password@localhost:5432/polymarket_nft"
```

### Вариант 2: Supabase (Бесплатно)

1. Перейдите на [supabase.com](https://supabase.com)
2. Создайте новый проект
3. В Project Settings → Database найдите Connection String
4. Скопируйте "Connection pooling" URI
5. Добавьте в `.env.local`:

```env
DATABASE_URL="postgresql://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:5432/postgres"
```

### Вариант 3: Railway (Бесплатно для начала)

1. Перейдите на [railway.app](https://railway.app)
2. Создайте новый проект
3. Добавьте PostgreSQL сервис
4. Скопируйте Database URL из переменных окружения
5. Добавьте в `.env.local`

### Вариант 4: Neon (Serverless Postgres)

1. Перейдите на [neon.tech](https://neon.tech)
2. Создайте новый проект
3. Скопируйте Connection String
4. Добавьте в `.env.local`

## 🔧 Настройка Prisma

### 1. Сгенерируйте Prisma Client

```bash
npx prisma generate
```

### 2. Примените миграции

```bash
npx prisma migrate dev --name init
```

Эта команда:
- Создаст таблицы в базе данных
- Применит схему из `prisma/schema.prisma`
- Сгенерирует типы TypeScript

### 3. (Опционально) Откройте Prisma Studio

Визуальный интерфейс для просмотра и редактирования данных:

```bash
npx prisma studio
```

Откроется в браузере на `http://localhost:5555`

## 📊 Схема базы данных

### Таблица `nft_claims`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | Serial | Уникальный ID |
| `ethAddress` | String(42) | ETH адрес (unique) |
| `proxyWallet` | String(42) | Proxy из Polymarket |
| `solanaAddress` | String(44) | Solana адрес для NFT |
| `status` | Enum | PENDING, PROCESSING, COMPLETED, FAILED |
| `createdAt` | DateTime | Дата создания |
| `updatedAt` | DateTime | Дата обновления |
| `processedAt` | DateTime? | Дата обработки |
| `mintTxHash` | String? | Хэш транзакции минта |
| `errorMessage` | Text? | Сообщение об ошибке |

### Таблица `rate_limits` (для rate limiting)

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | Serial | Уникальный ID |
| `identifier` | String | IP или ETH адрес |
| `count` | Int | Количество запросов |
| `windowStart` | DateTime | Начало временного окна |

## 🧪 Тестирование подключения

Создайте файл `test-db.ts`:

```typescript
import { prisma } from './lib/prisma';

async function testConnection() {
  try {
    await prisma.$connect();
    console.log('✅ Database connected successfully!');
    
    const count = await prisma.nftClaim.count();
    console.log(`📊 Total claims: ${count}`);
    
    await prisma.$disconnect();
  } catch (error) {
    console.error('❌ Database connection failed:', error);
    process.exit(1);
  }
}

testConnection();
```

Запустите:
```bash
npx tsx test-db.ts
```

## 🔄 Полезные команды Prisma

```bash
# Применить миграции
npx prisma migrate dev

# Применить миграции в production
npx prisma migrate deploy

# Сбросить базу данных (ОСТОРОЖНО!)
npx prisma migrate reset

# Сгенерировать Prisma Client
npx prisma generate

# Открыть Prisma Studio
npx prisma studio

# Проверить статус миграций
npx prisma migrate status

# Создать новую миграцию
npx prisma migrate dev --name your_migration_name

# Форматировать schema.prisma
npx prisma format
```

## 📝 Скрипт для сидирования данных (опционально)

Создайте `prisma/seed.ts`:

```typescript
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
  console.log('Seeding database...');

  // Добавьте тестовые данные если нужно
  // await prisma.nftClaim.create({ ... });

  console.log('✅ Seeding completed');
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
```

Добавьте в `package.json`:
```json
{
  "prisma": {
    "seed": "tsx prisma/seed.ts"
  }
}
```

Запустите:
```bash
npx prisma db seed
```

## 🚨 Troubleshooting

### Ошибка: "Can't reach database server"

**Причины:**
- PostgreSQL не запущен
- Неверный DATABASE_URL
- Firewall блокирует соединение

**Решение:**
```bash
# Проверьте статус PostgreSQL
# Windows:
pg_ctl status

# Linux/macOS:
sudo systemctl status postgresql
```

### Ошибка: "Authentication failed"

**Решение:**
- Проверьте правильность пароля в DATABASE_URL
- Убедитесь что пользователь существует
- Проверьте `pg_hba.conf` для разрешения соединений

### Ошибка: "Database does not exist"

**Решение:**
```bash
createdb polymarket_nft
```

### Ошибка: "Schema has diverged"

**Решение:**
```bash
# Сбросить и применить заново (УДАЛИТ ВСЕ ДАННЫЕ!)
npx prisma migrate reset

# Или создать новую миграцию
npx prisma migrate dev
```

## 🔒 Production Best Practices

1. **Используйте connection pooling**
   - Supabase: используйте pooler URL
   - Для других: используйте PgBouncer

2. **Бэкапы**
   ```bash
   # Создать бэкап
   pg_dump -U postgres polymarket_nft > backup.sql
   
   # Восстановить из бэкапа
   psql -U postgres polymarket_nft < backup.sql
   ```

3. **Мониторинг**
   - Настройте алерты на количество соединений
   - Мониторьте размер таблиц
   - Логируйте медленные запросы

4. **Индексы**
   - Уже добавлены в schema.prisma
   - Проверяйте query performance

5. **Миграции**
   - Всегда тестируйте на staging
   - Делайте бэкапы перед миграциями
   - Используйте `prisma migrate deploy` в production

## 📚 Дополнительные ресурсы

- [Prisma Documentation](https://www.prisma.io/docs)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Supabase Documentation](https://supabase.com/docs)
- [Railway Documentation](https://docs.railway.app)

## ✅ Чеклист готовности

- [ ] PostgreSQL установлен и запущен
- [ ] База данных создана
- [ ] `DATABASE_URL` добавлен в `.env.local`
- [ ] `npx prisma generate` выполнен успешно
- [ ] `npx prisma migrate dev` выполнен успешно
- [ ] Подключение тестировано
- [ ] Prisma Studio открывается (опционально)

После выполнения всех шагов база данных готова к использованию! 🎉
