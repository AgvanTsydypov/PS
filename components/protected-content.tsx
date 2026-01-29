'use client';

import { useEffect, useState } from 'react';
import { useAccount } from 'wagmi';

interface SessionInfo {
  isVerified: boolean;
  address: string | null;
  proxyWallet: string | null;
}

/**
 * Компонент для отображения защищенного контента
 * Проверяет сессию пользователя и показывает контент только верифицированным пользователям
 */
export function ProtectedContent({
  children,
  fallback,
}: {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { isConnected } = useAccount();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkSession = async () => {
      if (!isConnected) {
        setSession(null);
        setLoading(false);
        return;
      }

      try {
        const response = await fetch('/api/session');
        const data = await response.json();
        setSession(data);
      } catch (error) {
        console.error('Failed to check session:', error);
        setSession(null);
      } finally {
        setLoading(false);
      }
    };

    checkSession();
  }, [isConnected]);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
      </div>
    );
  }

  if (!session?.isVerified) {
    return (
      <>
        {fallback || (
          <div className="text-center p-8 bg-yellow-50 rounded-lg border border-yellow-200">
            <p className="text-yellow-800 font-medium">
              Доступ запрещен. Пожалуйста, пройдите верификацию.
            </p>
          </div>
        )}
      </>
    );
  }

  return <>{children}</>;
}
