'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAccount } from 'wagmi';

interface SessionData {
  isVerified: boolean;
  address: string | null;
  proxyWallet: string | null;
}

interface UseSessionResult {
  session: SessionData | null;
  isLoading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  logout: () => Promise<void>;
}

/**
 * Кастомный хук для работы с сессией пользователя
 * 
 * @example
 * ```tsx
 * const { session, isLoading, logout } = useSession();
 * 
 * if (session?.isVerified) {
 *   return <div>Добро пожаловать!</div>;
 * }
 * ```
 */
export function useSession(): UseSessionResult {
  const { isConnected } = useAccount();
  const [session, setSession] = useState<SessionData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSession = useCallback(async () => {
    if (!isConnected) {
      setSession(null);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/session');
      
      if (!response.ok) {
        throw new Error('Не удалось получить сессию');
      }

      const data = await response.json();
      setSession(data);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Ошибка загрузки сессии';
      setError(errorMessage);
      setSession(null);
    } finally {
      setIsLoading(false);
    }
  }, [isConnected]);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/session', { method: 'DELETE' });
      setSession(null);
    } catch (err) {
      console.error('Logout error:', err);
    }
  }, []);

  useEffect(() => {
    fetchSession();
  }, [fetchSession]);

  return {
    session,
    isLoading,
    error,
    refetch: fetchSession,
    logout,
  };
}
