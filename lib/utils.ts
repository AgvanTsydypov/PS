import { Address, getAddress, isAddress } from 'viem';

/**
 * Утилиты для работы с адресами и данными
 */

/**
 * Валидирует и нормализует Ethereum адрес
 * @param address - Адрес для проверки
 * @returns Нормализованный checksum адрес или null если невалидный
 */
export function validateAndNormalizeAddress(
  address: string
): Address | null {
  try {
    if (!isAddress(address)) {
      return null;
    }
    return getAddress(address);
  } catch {
    return null;
  }
}

/**
 * Проверяет, находится ли адрес в списке разрешенных
 * @param address - Адрес для проверки
 * @param allowedAddresses - Массив разрешенных адресов
 * @returns true если адрес разрешен
 */
export function isAddressAllowed(
  address: string,
  allowedAddresses: readonly string[]
): boolean {
  const normalizedAddress = validateAndNormalizeAddress(address);
  if (!normalizedAddress) return false;

  const normalizedAllowed = allowedAddresses
    .map((addr) => validateAndNormalizeAddress(addr))
    .filter((addr): addr is Address => addr !== null);

  return normalizedAllowed.includes(normalizedAddress);
}

/**
 * Форматирует адрес для отображения (сокращенный вид)
 * @param address - Полный адрес
 * @param startChars - Количество символов в начале (по умолчанию 6)
 * @param endChars - Количество символов в конце (по умолчанию 4)
 * @returns Форматированный адрес типа "0x1234...5678"
 */
export function formatAddress(
  address: string,
  startChars: number = 6,
  endChars: number = 4
): string {
  if (!address) return '';
  if (address.length <= startChars + endChars) return address;
  
  return `${address.slice(0, startChars)}...${address.slice(-endChars)}`;
}

/**
 * Задержка для асинхронных операций
 * @param ms - Миллисекунды задержки
 */
export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Безопасное парсинг JSON с обработкой ошибок
 * @param json - JSON строка
 * @param fallback - Значение по умолчанию при ошибке
 */
export function safeJsonParse<T>(json: string, fallback: T): T {
  try {
    return JSON.parse(json) as T;
  } catch {
    return fallback;
  }
}

/**
 * Генерирует случайный SESSION_SECRET нужной длины
 * @param length - Длина секрета (по умолчанию 32)
 */
export function generateSessionSecret(length: number = 32): string {
  const chars =
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*';
  let result = '';
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}
