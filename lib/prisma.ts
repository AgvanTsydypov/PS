import { PrismaClient } from '@prisma/client';

/**
 * Prisma Client Singleton
 * 
 * В development режиме создает только один экземпляр клиента
 * чтобы избежать множественных подключений при hot reload
 */

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined;
};

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: process.env.NODE_ENV === 'development' ? ['query', 'error', 'warn'] : ['error'],
  });

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma;

export default prisma;
