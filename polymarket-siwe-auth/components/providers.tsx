'use client';

import { WagmiProvider } from 'wagmi';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConnectKitProvider } from 'connectkit';
import { config } from '@/lib/wagmi-config';
import { useState } from 'react';

/**
 * Провайдеры для Web3 и ConnectKit
 * Оборачивают все приложение для предоставления контекста Wagmi, React Query и ConnectKit
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <WagmiProvider config={config}>
      <QueryClientProvider client={queryClient}>
        <ConnectKitProvider
          theme="auto"
          mode="auto"
          options={{
            disclaimer: (
              <>
                Подключая кошелек, вы соглашаетесь на верификацию через Polymarket API.
              </>
            ),
          }}
        >
          {children}
        </ConnectKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}
