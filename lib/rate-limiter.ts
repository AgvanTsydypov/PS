import { prisma } from './prisma';

interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: Date;
}

/**
 * Simple in-memory rate limiter для development
 * В production лучше использовать Redis
 */
const inMemoryStore = new Map<
  string,
  { count: number; resetAt: number }
>();

/**
 * Rate Limiter для защиты от спама
 * 
 * @param identifier - уникальный идентификатор (IP или ethAddress)
 * @param limit - максимальное количество запросов
 * @param windowMs - временное окно в миллисекундах
 */
export async function checkRateLimit(
  identifier: string,
  limit: number = 5,
  windowMs: number = 60 * 60 * 1000 // 1 час по умолчанию
): Promise<RateLimitResult> {
  const now = Date.now();
  const resetAt = new Date(now + windowMs);

  // Простая in-memory реализация для development
  const key = identifier;
  const existing = inMemoryStore.get(key);

  // Проверяем не истекло ли окно
  if (existing && existing.resetAt > now) {
    // Окно еще активно
    if (existing.count >= limit) {
      return {
        allowed: false,
        remaining: 0,
        resetAt: new Date(existing.resetAt),
      };
    }

    // Увеличиваем счетчик
    existing.count++;
    return {
      allowed: true,
      remaining: limit - existing.count,
      resetAt: new Date(existing.resetAt),
    };
  }

  // Создаем новое окно
  inMemoryStore.set(key, {
    count: 1,
    resetAt: now + windowMs,
  });

  // Очищаем старые записи
  for (const [k, v] of inMemoryStore.entries()) {
    if (v.resetAt < now) {
      inMemoryStore.delete(k);
    }
  }

  return {
    allowed: true,
    remaining: limit - 1,
    resetAt,
  };
}

/**
 * Database-based rate limiter (для production с Postgres)
 */
export async function checkRateLimitDB(
  identifier: string,
  limit: number = 5,
  windowMs: number = 60 * 60 * 1000
): Promise<RateLimitResult> {
  const now = new Date();
  const windowStart = new Date(now.getTime() - windowMs);

  try {
    // Получаем или создаем запись
    const record = await prisma.rateLimit.upsert({
      where: { identifier },
      create: {
        identifier,
        count: 1,
        windowStart: now,
      },
      update: {
        count: {
          increment: 1,
        },
      },
    });

    // Проверяем не истекло ли окно
    const isExpired = record.windowStart < windowStart;

    if (isExpired) {
      // Сбрасываем счетчик
      await prisma.rateLimit.update({
        where: { identifier },
        data: {
          count: 1,
          windowStart: now,
        },
      });

      return {
        allowed: true,
        remaining: limit - 1,
        resetAt: new Date(now.getTime() + windowMs),
      };
    }

    // Проверяем лимит
    const allowed = record.count <= limit;
    const remaining = Math.max(0, limit - record.count);

    return {
      allowed,
      remaining,
      resetAt: new Date(record.windowStart.getTime() + windowMs),
    };
  } catch (error) {
    console.error('Rate limit check error:', error);
    // В случае ошибки разрешаем запрос (fail-open)
    return {
      allowed: true,
      remaining: limit,
      resetAt: new Date(now.getTime() + windowMs),
    };
  }
}
