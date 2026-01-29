# Быстрая установка и настройка

## Шаг 1: Установка зависимостей

Убедитесь, что у вас установлен Node.js версии 18 или выше.

```bash
# Проверьте версию Node.js
node --version

# Установите зависимости
npm install
```

## Шаг 2: Настройка переменных окружения

### 1. Создайте файл .env.local

```bash
cp env.example .env.local
```

### 2. Сгенерируйте SESSION_SECRET

**Вариант A: Автоматическая генерация (рекомендуется)**

```bash
node scripts/generate-secret.js
```

Скопируйте сгенерированный ключ в `.env.local`

**Вариант B: Ручная генерация**

В терминале (Linux/Mac):
```bash
openssl rand -base64 32
```

В PowerShell (Windows):
```powershell
[Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

### 3. Получите WalletConnect Project ID

1. Перейдите на [WalletConnect Cloud](https://cloud.walletconnect.com)
2. Создайте новый проект
3. Скопируйте Project ID
4. Вставьте в `.env.local` как `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID`

### 4. Пример .env.local

```env
SESSION_SECRET=your-generated-secret-here-minimum-32-chars
NEXT_PUBLIC_APP_URL=http://localhost:3000
NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=your-walletconnect-project-id
```

## Шаг 3: Настройка разрешенных адресов

Откройте `lib/constants.ts` и добавьте ваши прокси-адреса:

```typescript
export const ALLOWED_PROXIES = [
  '0xВашПервыйАдрес',
  '0xВашВторойАдрес',
  // Добавьте больше адресов
] as const;
```

**Как найти свой proxy адрес:**

1. Откройте [https://polymarket.com](https://polymarket.com)
2. Подключите ваш кошелек
3. Откройте консоль браузера (F12)
4. Выполните запрос:
```javascript
fetch('https://gamma-api.polymarket.com/public-profile?address=YOUR_EOA_ADDRESS')
  .then(r => r.json())
  .then(d => console.log('Proxy Wallet:', d.proxyWallet))
```

Или используйте curl:
```bash
curl "https://gamma-api.polymarket.com/public-profile?address=YOUR_EOA_ADDRESS"
```

## Шаг 4: Запуск приложения

```bash
npm run dev
```

Откройте [http://localhost:3000](http://localhost:3000)

## Шаг 5: Тестирование

1. Нажмите "Connect Wallet"
2. Подключите кошелек через MetaMask или WalletConnect
3. Подпишите SIWE сообщение
4. Дождитесь результата верификации

### Тестовый сценарий

**Если ваш proxy адрес в списке:**
- ✅ Вы увидите "Доступ разрешен"
- ✅ Можете перейти на `/dashboard`

**Если ваш proxy адрес НЕ в списке:**
- ❌ Вы увидите "Доступ запрещен"
- ❌ Доступ к `/dashboard` будет заблокирован

## Возможные проблемы

### Ошибка: "SESSION_SECRET must be at least 32 characters"

**Решение:** Убедитесь, что ваш SESSION_SECRET в `.env.local` длиннее 32 символов.

### Ошибка: "Failed to fetch nonce"

**Решение:** Проверьте, что:
- Сервер запущен (`npm run dev`)
- `.env.local` существует и правильно настроен
- SESSION_SECRET установлен

### Ошибка: "WalletConnect Project ID not found"

**Решение:** Добавьте `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID` в `.env.local`

### Кошелек подключается, но ничего не происходит

**Решение:** Откройте консоль браузера (F12) и проверьте ошибки.

### "No proxy wallet found"

**Решение:** Убедитесь, что:
- У вас есть аккаунт на Polymarket
- Вы хотя бы раз торговали или взаимодействовали с платформой
- Используете правильный EOA адрес

## Production Deployment

### Vercel

1. Установите Vercel CLI:
```bash
npm i -g vercel
```

2. Деплой:
```bash
vercel
```

3. Добавьте переменные окружения в Vercel Dashboard:
   - `SESSION_SECRET`
   - `NEXT_PUBLIC_APP_URL` (ваш production URL)
   - `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID`

### Другие платформы

Убедитесь, что:
- Node.js версии 18+
- Установлены все environment variables
- `SESSION_SECRET` уникален и безопасен

## Безопасность в Production

- ✅ Используйте HTTPS
- ✅ Установите строгий CSP (Content Security Policy)
- ✅ Добавьте rate limiting для API
- ✅ Замените ALLOWED_PROXIES на базу данных
- ✅ Включите логирование и мониторинг
- ✅ Регулярно обновляйте зависимости

## Следующие шаги

После успешной настройки:

1. Кастомизируйте UI в `components/siwe-auth.tsx`
2. Добавьте свою бизнес-логику в Dashboard
3. Настройте дополнительные защищенные страницы
4. Интегрируйте с вашим backend API
5. Добавьте аналитику и мониторинг

## Полезные команды

```bash
# Разработка
npm run dev

# Сборка для production
npm run build

# Запуск production сервера
npm run start

# Проверка типов TypeScript
npx tsc --noEmit

# Генерация нового секрета
node scripts/generate-secret.js
```

## Дополнительная помощь

- [README.md](./README.md) - Полная документация
- [Polymarket API](https://docs.polymarket.com)
- [SIWE Specification](https://eips.ethereum.org/EIPS/eip-4361)
- [Next.js Docs](https://nextjs.org/docs)
