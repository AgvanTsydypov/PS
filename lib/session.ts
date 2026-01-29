import { getIronSession, IronSession } from 'iron-session';
import { cookies } from 'next/headers';

export interface SessionData {
  nonce?: string;
  siwe?: {
    address: string;
    chainId: number;
  };
  isVerified?: boolean;
  proxyWallet?: string;
}

// Конфигурация iron-session
const sessionOptions = {
  password: process.env.SESSION_SECRET!,
  cookieName: 'siwe-session',
  cookieOptions: {
    secure: process.env.NODE_ENV === 'production',
    httpOnly: true,
    sameSite: 'lax' as const,
    maxAge: 60 * 60 * 24 * 7, // 7 дней
    path: '/',
  },
};

export async function getSession(): Promise<IronSession<SessionData>> {
  if (!process.env.SESSION_SECRET || process.env.SESSION_SECRET.length < 32) {
    throw new Error('SESSION_SECRET must be at least 32 characters long');
  }

  return getIronSession<SessionData>(cookies(), sessionOptions);
}
