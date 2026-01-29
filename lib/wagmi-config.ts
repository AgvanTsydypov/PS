import { http, createConfig } from 'wagmi';
import { mainnet, polygon } from 'wagmi/chains';
import { injected } from 'wagmi/connectors';

// Простая конфигурация Wagmi без WalletConnect (только MetaMask/Injected)
export const config = createConfig({
  chains: [mainnet, polygon],
  connectors: [
    injected({
      shimDisconnect: true,
    }),
  ],
  transports: {
    [mainnet.id]: http(),
    [polygon.id]: http(),
  },
  ssr: true,
});

declare module 'wagmi' {
  interface Register {
    config: typeof config;
  }
}
