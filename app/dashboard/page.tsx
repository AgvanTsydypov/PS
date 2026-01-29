'use client';

import { ProtectedContent } from '@/components/protected-content';
import { useAccount } from 'wagmi';
import { useEffect, useState } from 'react';
import { formatAddress } from '@/lib/utils';

/**
 * Пример защищенной страницы Dashboard
 * Доступна только после успешной верификации
 */
export default function DashboardPage() {
  const { address } = useAccount();
  const [session, setSession] = useState<any>(null);

  useEffect(() => {
    const fetchSession = async () => {
      try {
        const response = await fetch('/api/session');
        const data = await response.json();
        setSession(data);
      } catch (error) {
        console.error('Failed to fetch session:', error);
      }
    };

    fetchSession();
  }, []);

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 p-8">
      <div className="max-w-4xl mx-auto">
        <div className="bg-white rounded-2xl shadow-xl p-8 mb-6">
          <h1 className="text-3xl font-bold text-gray-800 mb-2">
            Dashboard
          </h1>
          <p className="text-gray-600">
            Защищенная страница для верифицированных пользователей
          </p>
        </div>

        <ProtectedContent
          fallback={
            <div className="bg-white rounded-2xl shadow-xl p-8">
              <div className="text-center">
                <h2 className="text-2xl font-bold text-gray-800 mb-4">
                  Доступ запрещен
                </h2>
                <p className="text-gray-600 mb-6">
                  Для доступа к этой странице необходимо пройти верификацию.
                </p>
                <a
                  href="/"
                  className="inline-block bg-blue-500 hover:bg-blue-600 text-white font-medium py-2 px-6 rounded-lg transition"
                >
                  Вернуться на главную
                </a>
              </div>
            </div>
          }
        >
          <div className="space-y-6">
            {/* Информация о пользователе */}
            <div className="bg-white rounded-2xl shadow-xl p-8">
              <h2 className="text-2xl font-bold text-gray-800 mb-4">
                Информация о пользователе
              </h2>
              
              <div className="space-y-4">
                <div className="border-b pb-4">
                  <p className="text-sm text-gray-600 mb-1">
                    Ваш кошелек (EOA)
                  </p>
                  <p className="font-mono text-sm break-all">
                    {address || 'Не подключен'}
                  </p>
                  {address && (
                    <p className="text-xs text-gray-500 mt-1">
                      Короткий формат: {formatAddress(address)}
                    </p>
                  )}
                </div>

                {session?.proxyWallet && (
                  <div className="border-b pb-4">
                    <p className="text-sm text-gray-600 mb-1">
                      Proxy Wallet (Polymarket)
                    </p>
                    <p className="font-mono text-sm break-all">
                      {session.proxyWallet}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      Короткий формат: {formatAddress(session.proxyWallet)}
                    </p>
                  </div>
                )}

                <div>
                  <p className="text-sm text-gray-600 mb-1">Статус</p>
                  <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-green-100 text-green-800">
                    ✓ Верифицирован
                  </span>
                </div>
              </div>
            </div>

            {/* Защищенный контент */}
            <div className="bg-white rounded-2xl shadow-xl p-8">
              <h2 className="text-2xl font-bold text-gray-800 mb-4">
                Защищенный контент
              </h2>
              
              <div className="prose max-w-none">
                <p className="text-gray-700 mb-4">
                  Это пример защищенного контента, доступного только верифицированным пользователям.
                </p>
                
                <div className="bg-blue-50 border-l-4 border-blue-500 p-4 mb-4">
                  <p className="text-blue-800 font-medium">
                    Доступ получен!
                  </p>
                  <p className="text-blue-700 text-sm mt-1">
                    Ваш прокси-кошелек найден в списке разрешенных адресов.
                  </p>
                </div>

                <div className="grid md:grid-cols-2 gap-4 mt-6">
                  <div className="bg-gray-50 p-4 rounded-lg">
                    <h3 className="font-semibold text-gray-800 mb-2">
                      Функция 1
                    </h3>
                    <p className="text-gray-600 text-sm">
                      Здесь может быть функционал, доступный только верифицированным пользователям.
                    </p>
                  </div>
                  
                  <div className="bg-gray-50 p-4 rounded-lg">
                    <h3 className="font-semibold text-gray-800 mb-2">
                      Функция 2
                    </h3>
                    <p className="text-gray-600 text-sm">
                      Например, доступ к премиум-аналитике или эксклюзивным данным.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Действия */}
            <div className="bg-white rounded-2xl shadow-xl p-8">
              <h2 className="text-2xl font-bold text-gray-800 mb-4">
                Действия
              </h2>
              
              <div className="flex gap-4">
                <a
                  href="/"
                  className="inline-block bg-gray-200 hover:bg-gray-300 text-gray-800 font-medium py-2 px-6 rounded-lg transition"
                >
                  На главную
                </a>
                
                <button
                  onClick={async () => {
                    await fetch('/api/session', { method: 'DELETE' });
                    window.location.href = '/';
                  }}
                  className="bg-red-500 hover:bg-red-600 text-white font-medium py-2 px-6 rounded-lg transition"
                >
                  Выйти
                </button>
              </div>
            </div>
          </div>
        </ProtectedContent>
      </div>
    </div>
  );
}
