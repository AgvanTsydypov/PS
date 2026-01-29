interface PolymarketProfileResponse {
  proxyWallet?: string;
  // Другие поля API, которые могут присутствовать
  [key: string]: any;
}

/**
 * Получает информацию о профиле пользователя из Polymarket API
 * @param address - EOA адрес пользователя (кошелек, которым подписывали SIWE)
 * @returns Объект с информацией о профиле, включая proxyWallet
 */
export async function getPolymarketProfile(
  address: string
): Promise<PolymarketProfileResponse> {
  const url = `https://gamma-api.polymarket.com/public-profile?address=${address}`;

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      // Кэширование отключено для получения актуальных данных
      cache: 'no-store',
    });

    if (!response.ok) {
      throw new Error(
        `Polymarket API error: ${response.status} ${response.statusText}`
      );
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Error fetching Polymarket profile:', error);
    throw new Error('Failed to fetch profile from Polymarket API');
  }
}
