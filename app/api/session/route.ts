import { NextResponse } from 'next/server';
import { getSession } from '@/lib/session';

/**
 * API маршрут для получения информации о текущей сессии
 * GET /api/session
 * 
 * Возвращает информацию о сессии пользователя:
 * - isVerified: прошел ли пользователь верификацию
 * - address: адрес кошелька (EOA)
 * - proxyWallet: адрес прокси-кошелька из Polymarket
 */
export async function GET() {
  try {
    const session = await getSession();

    return NextResponse.json(
      {
        isVerified: session.isVerified || false,
        address: session.siwe?.address || null,
        proxyWallet: session.proxyWallet || null,
      },
      { status: 200 }
    );
  } catch (error) {
    console.error('Session error:', error);
    return NextResponse.json(
      { error: 'Failed to get session' },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/session
 * Удаляет сессию (logout)
 */
export async function DELETE() {
  try {
    const session = await getSession();
    session.destroy();

    return NextResponse.json({ success: true }, { status: 200 });
  } catch (error) {
    console.error('Logout error:', error);
    return NextResponse.json(
      { error: 'Failed to logout' },
      { status: 500 }
    );
  }
}
