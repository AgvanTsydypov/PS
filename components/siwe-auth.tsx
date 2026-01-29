'use client';

import { useEffect, useState } from 'react';
import { useAccount, useSignMessage } from 'wagmi';
import { SiweMessage } from 'siwe';
import { ConnectKitButton } from 'connectkit';

type AuthStatus =
  | 'disconnected'
  | 'connected'
  | 'signing'
  | 'verifying'
  | 'verified'
  | 'rejected';

interface VerificationResult {
  verified: boolean;
  proxy?: string;
  address?: string;
  error?: string;
}

/**
 * Компонент для аутентификации через SIWE и верификации через Polymarket
 * 
 * Процесс:
 * 1. Пользователь подключает кошелек через ConnectKit
 * 2. Автоматически запрашивается nonce с сервера
 * 3. Формируется SIWE сообщение и запрашивается подпись
 * 4. Подпись отправляется на сервер для верификации
 * 5. Сервер проверяет подпись и запрашивает proxyWallet из Polymarket
 * 6. Результат верификации отображается пользователю
 */
export function SiweAuth() {
  const { address, isConnected, chainId } = useAccount();
  const { signMessageAsync } = useSignMessage();
  
  const [status, setStatus] = useState<AuthStatus>('disconnected');
  const [verificationResult, setVerificationResult] =
    useState<VerificationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Автоматически запускаем процесс SIWE после подключения кошелька
  useEffect(() => {
    if (isConnected && address && status === 'disconnected') {
      setStatus('connected');
      handleSiweAuth();
    } else if (!isConnected) {
      setStatus('disconnected');
      setVerificationResult(null);
      setError(null);
    }
  }, [isConnected, address]);

  /**
   * Основная функция аутентификации через SIWE
   */
  const handleSiweAuth = async () => {
    if (!address || !chainId) return;

    try {
      setError(null);
      setStatus('signing');

      // Шаг 1: Запрашиваем nonce с сервера
      const nonceResponse = await fetch('/api/nonce');
      if (!nonceResponse.ok) {
        throw new Error('Failed to fetch nonce');
      }
      const { nonce } = await nonceResponse.json();

      // Шаг 2: Формируем SIWE сообщение
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

      // Шаг 3: Запрашиваем подпись у пользователя
      let signature: string;
      try {
        signature = await signMessageAsync({ message: messageString });
      } catch (signError) {
        setStatus('connected');
        setError('Подпись отклонена пользователем');
        return;
      }

      // Шаг 4: Отправляем подпись на сервер для верификации
      setStatus('verifying');
      const verifyResponse = await fetch('/api/verify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: messageString,
          signature: signature,
        }),
      });

      if (!verifyResponse.ok) {
        const errorData = await verifyResponse.json();
        throw new Error(errorData.error || 'Verification failed');
      }

      const result: VerificationResult = await verifyResponse.json();
      
      setVerificationResult(result);
      setStatus(result.verified ? 'verified' : 'rejected');

    } catch (err) {
      console.error('SIWE authentication error:', err);
      setError(
        err instanceof Error ? err.message : 'Произошла ошибка при верификации'
      );
      setStatus('connected');
    }
  };

  /**
   * Повторная попытка аутентификации
   */
  const handleRetry = () => {
    setError(null);
    setVerificationResult(null);
    handleSiweAuth();
  };

  /**
   * Выход из системы
   */
  const handleLogout = async () => {
    try {
      await fetch('/api/session', { method: 'DELETE' });
      setVerificationResult(null);
      setError(null);
      setStatus('disconnected');
    } catch (err) {
      console.error('Logout error:', err);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gradient-to-br from-purple-50 to-blue-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 max-w-md w-full">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-800 mb-2">
            Polymarket Auth
          </h1>
          <p className="text-gray-600">
            Верификация через SIWE и Polymarket API
          </p>
        </div>

        {/* Кнопка подключения кошелька */}
        <div className="mb-6 flex justify-center">
          <ConnectKitButton />
        </div>

        {/* Статусы и результаты */}
        <div className="space-y-4">
          {status === 'signing' && (
            <StatusCard
              type="info"
              title="Подпишите сообщение"
              description="Подтвердите подпись в кошельке для продолжения"
            />
          )}

          {status === 'verifying' && (
            <StatusCard
              type="info"
              title="Проверка в Polymarket..."
              description="Запрашиваем информацию о вашем прокси-кошельке"
            />
          )}

          {status === 'verified' && verificationResult?.verified && (
            <StatusCard
              type="success"
              title="Доступ разрешен ✓"
              description={
                <>
                  <p className="mb-2">Ваш прокси-кошелек найден в списке разрешенных</p>
                  <p className="text-xs break-all">
                    <strong>Proxy:</strong> {verificationResult.proxy}
                  </p>
                  <button
                    onClick={handleLogout}
                    className="mt-4 w-full bg-gray-200 hover:bg-gray-300 text-gray-800 font-medium py-2 px-4 rounded-lg transition"
                  >
                    Выйти
                  </button>
                </>
              }
            />
          )}

          {status === 'rejected' && verificationResult && !verificationResult.verified && (
            <StatusCard
              type="error"
              title="Доступ запрещен ✗"
              description={
                <>
                  <p className="mb-2">
                    {verificationResult.error || 'Ваш прокси-кошелек не найден в списке разрешенных'}
                  </p>
                  {verificationResult.proxy && (
                    <p className="text-xs break-all">
                      <strong>Ваш Proxy:</strong> {verificationResult.proxy}
                    </p>
                  )}
                  <button
                    onClick={handleRetry}
                    className="mt-4 w-full bg-red-500 hover:bg-red-600 text-white font-medium py-2 px-4 rounded-lg transition"
                  >
                    Попробовать снова
                  </button>
                </>
              }
            />
          )}

          {error && (
            <StatusCard
              type="error"
              title="Ошибка"
              description={
                <>
                  <p className="mb-2">{error}</p>
                  <button
                    onClick={handleRetry}
                    className="mt-2 w-full bg-red-500 hover:bg-red-600 text-white font-medium py-2 px-4 rounded-lg transition"
                  >
                    Попробовать снова
                  </button>
                </>
              }
            />
          )}

          {isConnected && status === 'connected' && !error && (
            <StatusCard
              type="info"
              title="Подключено"
              description={
                <>
                  <p className="text-xs break-all mb-3">
                    {address}
                  </p>
                  <button
                    onClick={handleSiweAuth}
                    className="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-2 px-4 rounded-lg transition"
                  >
                    Начать верификацию
                  </button>
                </>
              }
            />
          )}
        </div>

        {/* Информация о процессе */}
        {!isConnected && (
          <div className="mt-8 p-4 bg-gray-50 rounded-lg">
            <h3 className="font-semibold text-gray-700 mb-2">
              Как это работает:
            </h3>
            <ol className="text-sm text-gray-600 space-y-1 list-decimal list-inside">
              <li>Подключите ваш кошелек</li>
              <li>Подпишите SIWE сообщение</li>
              <li>Система проверит ваш прокси-кошелек в Polymarket</li>
              <li>Получите доступ, если адрес в списке разрешенных</li>
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Компонент для отображения статуса
 */
function StatusCard({
  type,
  title,
  description,
}: {
  type: 'success' | 'error' | 'info';
  title: string;
  description: React.ReactNode;
}) {
  const colors = {
    success: 'bg-green-50 border-green-200 text-green-800',
    error: 'bg-red-50 border-red-200 text-red-800',
    info: 'bg-blue-50 border-blue-200 text-blue-800',
  };

  return (
    <div className={`p-4 rounded-lg border-2 ${colors[type]}`}>
      <h3 className="font-semibold mb-2">{title}</h3>
      <div className="text-sm">{description}</div>
    </div>
  );
}
