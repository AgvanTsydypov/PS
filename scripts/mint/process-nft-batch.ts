/**
 * Скрипт для обработки партий NFT минта
 * 
 * Этот скрипт выбирает до 100 заявок со статусом PENDING,
 * обновляет их статус на PROCESSING, и подготавливает для минта.
 * 
 * Запуск: npx tsx scripts/mint/process-nft-batch.ts
 */

import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

interface BatchResult {
  processed: number;
  failed: number;
  claims: Array<{
    id: number;
    ethAddress: string;
    solanaAddress: string;
    proxyWallet: string;
  }>;
}

/**
 * Выбирает и обрабатывает партию заявок
 */
async function processBatch(batchSize: number = 100): Promise<BatchResult> {
  console.log(`\n🚀 Starting batch processing (max ${batchSize} claims)...`);
  
  try {
    // 1. Получаем партию PENDING заявок
    const pendingClaims = await prisma.nftClaim.findMany({
      where: { status: 'PENDING' },
      take: batchSize,
      orderBy: { createdAt: 'asc' },
    });

    if (pendingClaims.length === 0) {
      console.log('✅ No pending claims to process.');
      return { processed: 0, failed: 0, claims: [] };
    }

    console.log(`📋 Found ${pendingClaims.length} pending claims`);

    // 2. Обновляем статус на PROCESSING
    await prisma.nftClaim.updateMany({
      where: {
        id: { in: pendingClaims.map(c => c.id) },
      },
      data: {
        status: 'PROCESSING',
        updatedAt: new Date(),
      },
    });

    console.log(`⏳ Updated status to PROCESSING for ${pendingClaims.length} claims`);

    // 3. Подготовка данных для минта
    const claimsForMint = pendingClaims.map(claim => ({
      id: claim.id,
      ethAddress: claim.ethAddress,
      solanaAddress: claim.solanaAddress,
      proxyWallet: claim.proxyWallet,
    }));

    // 4. Здесь добавьте логику минта NFT
    // Пример:
    // for (const claim of claimsForMint) {
    //   try {
    //     const txHash = await mintNFT(claim.solanaAddress);
    //     await markAsCompleted(claim.id, txHash);
    //   } catch (error) {
    //     await markAsFailed(claim.id, error.message);
    //   }
    // }

    console.log('\n📦 Claims ready for minting:');
    console.table(claimsForMint);

    return {
      processed: pendingClaims.length,
      failed: 0,
      claims: claimsForMint,
    };
  } catch (error) {
    console.error('❌ Error processing batch:', error);
    throw error;
  }
}

/**
 * Отмечает заявку как успешно обработанную
 */
async function markAsCompleted(claimId: number, mintTxHash: string) {
  await prisma.nftClaim.update({
    where: { id: claimId },
    data: {
      status: 'COMPLETED',
      processedAt: new Date(),
      mintTxHash,
      updatedAt: new Date(),
    },
  });
  console.log(`✅ Claim ${claimId} marked as COMPLETED`);
}

/**
 * Отмечает заявку как неудачную
 */
async function markAsFailed(claimId: number, errorMessage: string) {
  await prisma.nftClaim.update({
    where: { id: claimId },
    data: {
      status: 'FAILED',
      errorMessage,
      processedAt: new Date(),
      updatedAt: new Date(),
    },
  });
  console.log(`❌ Claim ${claimId} marked as FAILED: ${errorMessage}`);
}

/**
 * Получает статистику по заявкам
 */
async function getStatistics() {
  const stats = await prisma.nftClaim.groupBy({
    by: ['status'],
    _count: true,
  });

  console.log('\n📊 Claims Statistics:');
  console.table(
    stats.map(s => ({
      Status: s.status,
      Count: s._count,
    }))
  );

  const total = await prisma.nftClaim.count();
  console.log(`\nTotal claims: ${total}`);
}

/**
 * Пример функции минта (заглушка)
 * Замените на реальную логику минта
 */
async function mintNFT(solanaAddress: string): Promise<string> {
  // TODO: Реализовать логику минта NFT на Solana
  // Пример:
  // 1. Создать транзакцию
  // 2. Подписать транзакцию
  // 3. Отправить транзакцию
  // 4. Дождаться подтверждения
  // 5. Вернуть transaction hash
  
  console.log(`Minting NFT to ${solanaAddress}...`);
  
  // Имитация минта (для тестирования)
  await new Promise(resolve => setTimeout(resolve, 1000));
  
  // Вернуть фейковый transaction hash
  return 'mock_tx_hash_' + Math.random().toString(36).substr(2, 9);
}

/**
 * Главная функция
 */
async function main() {
  console.log('='.repeat(60));
  console.log('NFT Batch Processing Script');
  console.log('='.repeat(60));

  try {
    // Показать текущую статистику
    await getStatistics();

    // Обработать партию
    const result = await processBatch(100);

    console.log('\n' + '='.repeat(60));
    console.log('📈 Batch Processing Summary:');
    console.log('='.repeat(60));
    console.log(`✅ Processed: ${result.processed}`);
    console.log(`❌ Failed: ${result.failed}`);
    console.log('='.repeat(60));

    // Пример: Обработка каждой заявки
    // Раскомментируйте когда будет готова логика минта
    /*
    for (const claim of result.claims) {
      try {
        console.log(`\n🎨 Minting NFT for ${claim.solanaAddress}...`);
        const txHash = await mintNFT(claim.solanaAddress);
        await markAsCompleted(claim.id, txHash);
      } catch (error: any) {
        console.error(`Error minting for claim ${claim.id}:`, error);
        await markAsFailed(claim.id, error.message);
      }
    }
    */

    // Показать обновленную статистику
    await getStatistics();

  } catch (error) {
    console.error('Fatal error:', error);
    process.exit(1);
  } finally {
    await prisma.$disconnect();
  }
}

// Запуск скрипта
if (require.main === module) {
  main()
    .then(() => {
      console.log('\n✅ Script completed successfully');
      process.exit(0);
    })
    .catch((error) => {
      console.error('\n❌ Script failed:', error);
      process.exit(1);
    });
}

export { processBatch, markAsCompleted, markAsFailed, getStatistics };
