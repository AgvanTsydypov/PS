import { NextRequest, NextResponse } from 'next/server';
import { getSession } from '@/lib/session';
import { prisma } from '@/lib/prisma';
import { validateSolanaAddressWithError } from '@/lib/solana-validator';
import { checkRateLimit } from '@/lib/rate-limiter';

/**
 * POST /api/claim-nft
 * Endpoint для отправки заявки на получение NFT
 * 
 * Требует:
 * - Активную SIWE сессию
 * - Верифицированный статус (isVerified === true)
 * - Валидный Solana адрес в теле запроса
 */
export async function POST(request: NextRequest) {
  try {
    // 1. Проверка сессии
    const session = await getSession();

    if (!session.isVerified || !session.siwe?.address || !session.proxyWallet) {
      return NextResponse.json(
        { 
          success: false, 
          error: 'Unauthorized. Please complete SIWE verification first.' 
        },
        { status: 401 }
      );
    }

    const ethAddress = session.siwe.address;
    const proxyWallet = session.proxyWallet;

    // 2. Rate Limiting (по ETH адресу)
    const rateLimitCheck = await checkRateLimit(
      `claim:${ethAddress}`,
      3,  // максимум 3 запроса
      60 * 60 * 1000  // в течение 1 часа
    );

    if (!rateLimitCheck.allowed) {
      return NextResponse.json(
        {
          success: false,
          error: 'Rate limit exceeded. Please try again later.',
          resetAt: rateLimitCheck.resetAt,
        },
        { 
          status: 429,
          headers: {
            'X-RateLimit-Limit': '3',
            'X-RateLimit-Remaining': '0',
            'X-RateLimit-Reset': rateLimitCheck.resetAt.toISOString(),
          }
        }
      );
    }

    // 3. Получение данных из запроса
    const body = await request.json();
    const { solanaAddress } = body;

    // 4. Валидация Solana адреса
    const validation = validateSolanaAddressWithError(solanaAddress);
    if (!validation.valid) {
      return NextResponse.json(
        {
          success: false,
          error: validation.error || 'Invalid Solana address',
        },
        { status: 400 }
      );
    }

    // Нормализуем адрес (trim)
    const normalizedSolanaAddress = solanaAddress.trim();

    // 5. Сохранение в базу данных (upsert)
    // Пользователь может обновить свой Solana адрес пока статус PENDING
    const existingClaim = await prisma.nftClaim.findUnique({
      where: { ethAddress },
    });

    // Если заявка уже обрабатывается или завершена, не позволяем обновление
    if (existingClaim && existingClaim.status !== 'PENDING') {
      return NextResponse.json(
        {
          success: false,
          error: `Cannot update claim. Current status: ${existingClaim.status}`,
          claim: {
            solanaAddress: existingClaim.solanaAddress,
            status: existingClaim.status,
            createdAt: existingClaim.createdAt,
          },
        },
        { status: 409 } // Conflict
      );
    }

    // Создаем или обновляем заявку
    const claim = await prisma.nftClaim.upsert({
      where: { ethAddress },
      create: {
        ethAddress,
        proxyWallet,
        solanaAddress: normalizedSolanaAddress,
        status: 'PENDING',
      },
      update: {
        solanaAddress: normalizedSolanaAddress,
        updatedAt: new Date(),
      },
    });

    // 6. Возвращаем успешный ответ
    return NextResponse.json(
      {
        success: true,
        message: 'NFT claim submitted successfully!',
        claim: {
          id: claim.id,
          solanaAddress: claim.solanaAddress,
          status: claim.status,
          createdAt: claim.createdAt,
        },
      },
      { 
        status: existingClaim ? 200 : 201,
        headers: {
          'X-RateLimit-Limit': '3',
          'X-RateLimit-Remaining': rateLimitCheck.remaining.toString(),
          'X-RateLimit-Reset': rateLimitCheck.resetAt.toISOString(),
        }
      }
    );
  } catch (error: any) {
    console.error('Claim NFT error:', error);
    
    return NextResponse.json(
      {
        success: false,
        error: 'Internal server error',
        details: process.env.NODE_ENV === 'development' ? error.message : undefined,
      },
      { status: 500 }
    );
  }
}

/**
 * GET /api/claim-nft
 * Получить информацию о текущей заявке пользователя
 */
export async function GET() {
  try {
    const session = await getSession();

    if (!session.isVerified || !session.siwe?.address) {
      return NextResponse.json(
        { 
          success: false, 
          error: 'Unauthorized' 
        },
        { status: 401 }
      );
    }

    const ethAddress = session.siwe.address;

    // Ищем заявку пользователя
    const claim = await prisma.nftClaim.findUnique({
      where: { ethAddress },
      select: {
        id: true,
        solanaAddress: true,
        status: true,
        createdAt: true,
        updatedAt: true,
        processedAt: true,
        mintTxHash: true,
      },
    });

    if (!claim) {
      return NextResponse.json(
        {
          success: true,
          claim: null,
        },
        { status: 200 }
      );
    }

    return NextResponse.json(
      {
        success: true,
        claim,
      },
      { status: 200 }
    );
  } catch (error: any) {
    console.error('Get claim error:', error);
    
    return NextResponse.json(
      {
        success: false,
        error: 'Internal server error',
      },
      { status: 500 }
    );
  }
}
