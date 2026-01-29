'use client';

import { useState, useCallback } from 'react';
import { useAccount, useSignMessage } from 'wagmi';
import { SiweMessage } from 'siwe';

interface UseSiweAuthResult {
  authenticate: () => Promise<AuthResult>;
  isLoading: boolean;
  error: string | null;
}

interface AuthResult {
  success: boolean;
  verified?: boolean;
  proxy?: string;
  address?: string;
  error?: string;
}

/**
 * Кастомный хук для упрощенной работы с SIWE аутентификацией
 * 
 * @example
 * ```tsx
 * const { authenticate, isLoading, error } = useSiweAuth();
 * 
 * const handleAuth = async () => {
 *   const result = await authenticate();
 *   if (result.success && result.verified) {
 *     console.log('Успешная верификация!');
 *   }
 * };
 * ```
 */
export function useSiweAuth(): UseSiweAuthResult {
  const { address, chainId } = useAccount();
  const { signMessageAsync } = useSignMessage();
  
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const authenticate = useCallback(async (): Promise<AuthResult> => {
    if (!address || !chainId) {
      setError('Кошелек не подключен');
      return { success: false, error: 'Кошелек не подключен' };
    }

    setIsLoading(true);
    setError(null);

    try {
      // Шаг 1: Получаем nonce
      const nonceResponse = await fetch('/api/nonce');
      if (!nonceResponse.ok) {
        throw new Error('Не удалось получить nonce');
      }
      const { nonce } = await nonceResponse.json();

      // Шаг 2: Создаем SIWE сообщение
      const message = new SiweMessage({
        domain: window.location.host,
        address: address,
        statement: 'Sign in to verify your Polymarket account',
        uri: window.location.origin,
        version: '1',
        chainId: chainId,
        nonce: nonce,
      });

      const messageString = message.prepareMessage();

      // Шаг 3: Подписываем сообщение
      let signature: string;
      try {
        signature = await signMessageAsync({ message: messageString });
      } catch (signError) {
        setError('Подпись отклонена');
        setIsLoading(false);
        return { success: false, error: 'Подпись отклонена' };
      }

      // Шаг 4: Отправляем на верификацию
      const verifyResponse = await fetch('/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: messageString,
          signature: signature,
        }),
      });

      if (!verifyResponse.ok) {
        const errorData = await verifyResponse.json();
        throw new Error(errorData.error || 'Ошибка верификации');
      }

      const result = await verifyResponse.json();

      setIsLoading(false);
      return {
        success: true,
        verified: result.verified,
        proxy: result.proxy,
        address: result.address,
      };

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Неизвестная ошибка';
      setError(errorMessage);
      setIsLoading(false);
      return { success: false, error: errorMessage };
    }
  }, [address, chainId, signMessageAsync]);

  return {
    authenticate,
    isLoading,
    error,
  };
}
