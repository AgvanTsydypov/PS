import { NextRequest, NextResponse } from 'next/server';
import { SiweMessage } from 'siwe';
import { getAddress } from 'viem';
import { getSession } from '@/lib/session';
import { getPolymarketProfile } from '@/lib/polymarket';
import { ALLOWED_PROXIES } from '@/lib/constants';

/**
 * API маршрут для верификации SIWE подписи и проверки доступа через Polymarket
 * POST /api/verify
 * 
 * Body: { message: string, signature: string }
 * 
 * Процесс верификации:
 * 1. Проверяет SIWE подпись на сервере
 * 2. Запрашивает proxyWallet из Polymarket API
 * 3. Сверяет proxyWallet со списком разрешенных адресов
 * 4. Сохраняет результат в сессии
 */
export async function POST(request: NextRequest) {
  try {
    const { message, signature } = await request.json();

    console.log('Received message type:', typeof message);
    console.log('Received message:', message);

    if (!message || !signature) {
      return NextResponse.json(
        { error: 'Message and signature are required' },
        { status: 400 }
      );
    }

    // Получаем сессию
    const session = await getSession();

    if (!session.nonce) {
      return NextResponse.json(
        { error: 'No nonce found. Please request a nonce first.' },
        { status: 400 }
      );
    }

    // Парсим SIWE сообщение из строки
    let siweMessage: SiweMessage;
    try {
      siweMessage = new SiweMessage(message);
    } catch (parseError) {
      console.error('Failed to parse SIWE message:', parseError);
      return NextResponse.json(
        { error: 'Invalid SIWE message format' },
        { status: 400 }
      );
    }

    // Верифицируем подпись
    const fields = await siweMessage.verify({
      signature,
      nonce: session.nonce,
    });

    if (!fields.success) {
      return NextResponse.json(
        { error: 'Invalid signature' },
        { status: 401 }
      );
    }

    // Получаем адрес пользователя (EOA)
    const userAddress = siweMessage.address;
    
    // Нормализуем адрес через viem для checksum
    const checksumAddress = getAddress(userAddress);

    // Запрашиваем информацию из Polymarket API
    let polymarketProfile;
    try {
      polymarketProfile = await getPolymarketProfile(checksumAddress);
    } catch (error) {
      console.error('Polymarket API error:', error);
      return NextResponse.json(
        { error: 'Failed to fetch profile from Polymarket' },
        { status: 500 }
      );
    }

    // Проверяем наличие proxyWallet в ответе
    if (!polymarketProfile.proxyWallet) {
      return NextResponse.json(
        {
          verified: false,
          error: 'No proxy wallet found for this address',
        },
        { status: 200 }
      );
    }

    // Нормализуем proxy адрес
    const proxyAddress = getAddress(polymarketProfile.proxyWallet);

    // Проверяем, входит ли proxy в список разрешенных
    // Нормализуем все адреса в ALLOWED_PROXIES для корректного сравнения
    const normalizedAllowedProxies = ALLOWED_PROXIES.map((addr) =>
      getAddress(addr)
    );
    
    const isAllowed = normalizedAllowedProxies.includes(proxyAddress);

    // Сохраняем результат верификации в сессии
    session.siwe = {
      address: checksumAddress,
      chainId: siweMessage.chainId,
    };
    session.isVerified = isAllowed;
    session.proxyWallet = proxyAddress;
    
    // Очищаем nonce после использования
    session.nonce = undefined;
    
    await session.save();

    return NextResponse.json(
      {
        verified: isAllowed,
        proxy: proxyAddress,
        address: checksumAddress,
      },
      { status: 200 }
    );
  } catch (error) {
    console.error('Verification error:', error);
    return NextResponse.json(
      { error: 'Verification failed' },
      { status: 500 }
    );
  }
}
