import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * Middleware для защиты маршрутов
 * 
 * В данном примере middleware не используется, но вы можете
 * добавить проверку сессии для защиты определенных страниц.
 * 
 * Пример использования:
 * - Проверка сессии перед доступом к защищенным маршрутам
 * - Rate limiting для API endpoints
 * - Логирование запросов
 */

export function middleware(request: NextRequest) {
  // Пример: Rate limiting для API
  const apiPath = request.nextUrl.pathname.startsWith('/api/');
  
  if (apiPath) {
    // Здесь можно добавить логику rate limiting
    // Например, используя Redis или in-memory хранилище
    
    // Добавляем заголовки для CORS если нужно
    const response = NextResponse.next();
    
    // Security headers
    response.headers.set('X-Content-Type-Options', 'nosniff');
    response.headers.set('X-Frame-Options', 'DENY');
    response.headers.set('X-XSS-Protection', '1; mode=block');
    
    return response;
  }

  return NextResponse.next();
}

// Настройка путей для middleware
export const config = {
  matcher: [
    /*
     * Применяется ко всем маршрутам кроме:
     * - api (обрабатывается отдельно)
     * - _next/static (статические файлы)
     * - _next/image (оптимизация изображений)
     * - favicon.ico (иконка)
     */
    '/((?!_next/static|_next/image|favicon.ico).*)',
  ],
};
