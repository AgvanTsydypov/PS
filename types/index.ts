/**
 * Типы для системы аутентификации SIWE + Polymarket
 */

import { Address } from 'viem';

/**
 * Статус аутентификации пользователя
 */
export type AuthStatus =
  | 'disconnected'  // Кошелек не подключен
  | 'connected'     // Кошелек подключен, ожидание действий
  | 'signing'       // Пользователь подписывает SIWE сообщение
  | 'verifying'     // Проверка через Polymarket API
  | 'verified'      // Успешная верификация
  | 'rejected';     // Верификация не прошла

/**
 * Результат верификации от API
 */
export interface VerificationResult {
  verified: boolean;
  proxy?: Address;
  address?: Address;
  error?: string;
}

/**
 * Информация о сессии пользователя
 */
export interface SessionInfo {
  isVerified: boolean;
  address: Address | null;
  proxyWallet: Address | null;
}

/**
 * Ответ от Polymarket API
 */
export interface PolymarketProfile {
  proxyWallet?: string;
  username?: string;
  profilePicture?: string;
  bio?: string;
  // Другие возможные поля
  [key: string]: any;
}

/**
 * Конфигурация для SIWE сообщения
 */
export interface SiweConfig {
  domain: string;
  uri: string;
  statement: string;
  version: '1';
  chainId: number;
}

/**
 * Nonce ответ от API
 */
export interface NonceResponse {
  nonce: string;
}

/**
 * Тело запроса для верификации
 */
export interface VerifyRequest {
  message: string;
  signature: string;
}
