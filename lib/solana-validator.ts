import { PublicKey } from '@solana/web3.js';

/**
 * Валидация Solana адреса
 * 
 * Проверяет что строка является валидным Solana публичным ключом
 * @param address - адрес для проверки
 * @returns true если адрес валиден
 */
export function isValidSolanaAddress(address: string): boolean {
  try {
    // Проверяем базовые требования
    if (!address || typeof address !== 'string') {
      return false;
    }

    // Solana адреса обычно 32-44 символа (base58)
    if (address.length < 32 || address.length > 44) {
      return false;
    }

    // Пытаемся создать PublicKey
    new PublicKey(address);
    return true;
  } catch (error) {
    return false;
  }
}

/**
 * Нормализует Solana адрес
 * @param address - адрес для нормализации
 * @returns нормализованный адрес или null если невалиден
 */
export function normalizeSolanaAddress(address: string): string | null {
  try {
    const pubkey = new PublicKey(address);
    return pubkey.toBase58();
  } catch {
    return null;
  }
}

/**
 * Валидирует и возвращает ошибку если адрес невалиден
 */
export function validateSolanaAddressWithError(
  address: string
): { valid: boolean; error?: string } {
  if (!address || address.trim() === '') {
    return { valid: false, error: 'Solana address is required' };
  }

  if (typeof address !== 'string') {
    return { valid: false, error: 'Solana address must be a string' };
  }

  const trimmed = address.trim();

  if (trimmed.length < 32 || trimmed.length > 44) {
    return {
      valid: false,
      error: 'Solana address must be between 32 and 44 characters',
    };
  }

  // Проверяем что содержит только base58 символы
  const base58Regex = /^[1-9A-HJ-NP-Za-km-z]+$/;
  if (!base58Regex.test(trimmed)) {
    return {
      valid: false,
      error: 'Solana address contains invalid characters',
    };
  }

  try {
    new PublicKey(trimmed);
    return { valid: true };
  } catch (error) {
    return {
      valid: false,
      error: 'Invalid Solana address format',
    };
  }
}
