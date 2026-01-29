import { NextResponse } from 'next/server';
import { getSession } from '@/lib/session';
import { generateNonce } from 'siwe';

/**
 * API маршрут для генерации nonce
 * GET /api/nonce
 * 
 * Генерирует случайный nonce и сохраняет его в сессии пользователя.
 * Этот nonce используется для предотвращения replay-атак при подписи SIWE сообщения.
 */
export async function GET() {
  try {
    const session = await getSession();
    
    // Генерируем новый nonce
    const nonce = generateNonce();
    
    // Сохраняем nonce в сессии
    session.nonce = nonce;
    await session.save();

    return NextResponse.json({ nonce }, { status: 200 });
  } catch (error) {
    console.error('Error generating nonce:', error);
    return NextResponse.json(
      { error: 'Failed to generate nonce' },
      { status: 500 }
    );
  }
}
