'use client';

import { useState, useEffect } from 'react';
import { validateSolanaAddressWithError } from '@/lib/solana-validator';
import { LoadingSpinner } from './loading-spinner';

interface ClaimStatus {
  id: number;
  solanaAddress: string;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  createdAt: string;
  updatedAt: string;
  processedAt?: string;
  mintTxHash?: string;
}

interface ClaimFormProps {
  /**
   * Показывать форму только если пользователь верифицирован
   */
  isVerified: boolean;
}

/**
 * Форма для сбора Solana адресов для минта NFT
 * Включает валидацию в реальном времени и обработку статусов
 */
export function SolanaClaimForm({ isVerified }: ClaimFormProps) {
  const [solanaAddress, setSolanaAddress] = useState('');
  const [validationError, setValidationError] = useState<string>('');
  const [isValidating, setIsValidating] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string>('');
  const [existingClaim, setExistingClaim] = useState<ClaimStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [showSuccess, setShowSuccess] = useState(false);

  // Загрузка существующей заявки при монтировании
  useEffect(() => {
    if (!isVerified) {
      setIsLoading(false);
      return;
    }

    const fetchExistingClaim = async () => {
      try {
        const response = await fetch('/api/claim-nft');
        const data = await response.json();
        
        if (data.success && data.claim) {
          setExistingClaim(data.claim);
          setSolanaAddress(data.claim.solanaAddress);
        }
      } catch (error) {
        console.error('Failed to fetch existing claim:', error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchExistingClaim();
  }, [isVerified]);

  // Валидация в реальном времени
  const handleAddressChange = (value: string) => {
    setSolanaAddress(value);
    setValidationError('');
    setSubmitError('');

    // Не показываем ошибки пока пользователь печатает
    if (!value) {
      return;
    }

    // Дебаунс валидации
    setIsValidating(true);
    const timeoutId = setTimeout(() => {
      const validation = validateSolanaAddressWithError(value);
      if (!validation.valid && value.trim().length > 0) {
        setValidationError(validation.error || 'Invalid address');
      }
      setIsValidating(false);
    }, 300);

    return () => clearTimeout(timeoutId);
  };

  // Отправка заявки
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError('');

    // Финальная валидация
    const validation = validateSolanaAddressWithError(solanaAddress);
    if (!validation.valid) {
      setValidationError(validation.error || 'Invalid address');
      return;
    }

    setIsSubmitting(true);

    try {
      const response = await fetch('/api/claim-nft', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          solanaAddress: solanaAddress.trim(),
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to submit claim');
      }

      // Успешная отправка
      setExistingClaim(data.claim);
      setShowSuccess(true);
      
      // Скрываем сообщение об успехе через 5 секунд
      setTimeout(() => setShowSuccess(false), 5000);
    } catch (error: any) {
      setSubmitError(error.message || 'Failed to submit claim. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Если пользователь не верифицирован
  if (!isVerified) {
    return null;
  }

  // Загрузка
  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoadingSpinner />
      </div>
    );
  }

  // Заявка уже обработана или в процессе
  if (existingClaim && existingClaim.status !== 'PENDING') {
    return (
      <div className="bg-white rounded-2xl shadow-xl p-8">
        <h2 className="text-2xl font-bold text-gray-800 mb-4">
          NFT Claim Status
        </h2>

        <div className="space-y-4">
          {/* Статус */}
          <div className="flex items-center gap-3">
            <div className={`px-4 py-2 rounded-full text-sm font-medium ${
              existingClaim.status === 'COMPLETED' 
                ? 'bg-green-100 text-green-800' 
                : existingClaim.status === 'PROCESSING'
                ? 'bg-blue-100 text-blue-800 animate-pulse'
                : 'bg-red-100 text-red-800'
            }`}>
              {existingClaim.status === 'COMPLETED' && '✓ Completed'}
              {existingClaim.status === 'PROCESSING' && '⏳ Processing'}
              {existingClaim.status === 'FAILED' && '✗ Failed'}
            </div>
          </div>

          {/* Информация */}
          <div className="bg-gray-50 rounded-lg p-4 space-y-2">
            <div>
              <p className="text-sm text-gray-600">Solana Address:</p>
              <p className="font-mono text-sm break-all text-gray-900">{existingClaim.solanaAddress}</p>
            </div>
            <div>
              <p className="text-sm text-gray-600">Submitted:</p>
              <p className="text-sm">{new Date(existingClaim.createdAt).toLocaleString()}</p>
            </div>
            {existingClaim.processedAt && (
              <div>
                <p className="text-sm text-gray-600">Processed:</p>
                <p className="text-sm">{new Date(existingClaim.processedAt).toLocaleString()}</p>
              </div>
            )}
            {existingClaim.mintTxHash && (
              <div>
                <p className="text-sm text-gray-600">Transaction:</p>
                <a
                  href={`https://solscan.io/tx/${existingClaim.mintTxHash}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:text-blue-800 text-sm font-mono break-all"
                >
                  {existingClaim.mintTxHash}
                </a>
              </div>
            )}
          </div>

          {existingClaim.status === 'COMPLETED' && (
            <div className="bg-green-50 border-l-4 border-green-500 p-4">
              <p className="text-green-800 font-medium">🎉 NFT Successfully Minted!</p>
              <p className="text-green-700 text-sm mt-1">
                Your NFT has been sent to your Solana address.
              </p>
            </div>
          )}

          {existingClaim.status === 'PROCESSING' && (
            <div className="bg-blue-50 border-l-4 border-blue-500 p-4">
              <p className="text-blue-800 font-medium">⏳ Mint in Progress</p>
              <p className="text-blue-700 text-sm mt-1">
                Your NFT is being minted. This usually takes a few minutes.
              </p>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Форма ввода адреса
  return (
    <div className="bg-white rounded-2xl shadow-xl p-8">
      <h2 className="text-2xl font-bold text-gray-800 mb-2">
        Запросить NFT
      </h2>
      <p className="text-gray-600 mb-6">
        Введите ваш Solana адрес для получения NFT. Минт проходит ежедневными партиями по 100 штук.
      </p>

      {/* Сообщение об успехе */}
      {showSuccess && (
        <div className="mb-6 bg-green-50 border-l-4 border-green-500 p-4 rounded animate-fade-in">
          <p className="text-green-800 font-medium mb-1">
            ✅ Адрес принят!
          </p>
          <p className="text-green-700 text-sm">
            Адрес <span className="font-mono text-green-900">{solanaAddress}</span> успешно добавлен. 
            Вы включены в ближайшую очередь на минт (партии по 100 штук).
          </p>
        </div>
      )}

      {/* Форма */}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="solanaAddress" className="block text-sm font-medium text-gray-700 mb-2">
            Solana Wallet Address
          </label>
          
          <div className="relative">
            <input
              id="solanaAddress"
              type="text"
              value={solanaAddress}
              onChange={(e) => handleAddressChange(e.target.value)}
              placeholder="Например: 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
              disabled={isSubmitting}
              className={`w-full px-4 py-3 border rounded-lg font-mono text-sm text-gray-900
                focus:ring-2 focus:ring-blue-500 focus:border-transparent
                disabled:bg-gray-100 disabled:cursor-not-allowed
                placeholder:text-gray-400
                ${validationError ? 'border-red-500' : 'border-gray-300'}
              `}
            />
            
            {/* Индикатор валидации */}
            {isValidating && (
              <div className="absolute right-3 top-3">
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-500" />
              </div>
            )}
            
            {!isValidating && solanaAddress && !validationError && (
              <div className="absolute right-3 top-3 text-green-500">
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              </div>
            )}
          </div>

          {/* Ошибка валидации */}
          {validationError && (
            <p className="mt-2 text-sm text-red-600">
              {validationError}
            </p>
          )}

          {/* Подсказка */}
          <p className="mt-2 text-xs text-gray-500">
            Solana адрес состоит из 32-44 символов (base58). Вы можете обновить адрес до начала обработки.
          </p>
        </div>

        {/* Ошибка отправки */}
        {submitError && (
          <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded">
            <p className="text-red-800 text-sm">{submitError}</p>
          </div>
        )}

        {/* Кнопка отправки */}
        <button
          type="submit"
          disabled={isSubmitting || !!validationError || !solanaAddress || isValidating}
          className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-3 px-6 rounded-lg
            transition duration-200 disabled:bg-gray-300 disabled:cursor-not-allowed
            flex items-center justify-center gap-2"
        >
          {isSubmitting ? (
            <>
              <LoadingSpinner />
              <span>Отправка...</span>
            </>
          ) : existingClaim ? (
            'Обновить Solana адрес'
          ) : (
            'Запросить NFT'
          )}
        </button>
      </form>

      {/* Информационный блок */}
      <div className="mt-6 bg-blue-50 rounded-lg p-4">
        <h3 className="font-semibold text-blue-900 mb-2">ℹ️ Как это работает?</h3>
        <ul className="text-sm text-blue-800 space-y-1">
          <li>• Введите ваш Solana адрес и нажмите "Запросить NFT"</li>
          <li>• Заявки обрабатываются ежедневно партиями по 100 штук</li>
          <li>• Вы можете обновить адрес до начала обработки</li>
          <li>• После минта NFT будет отправлен на указанный адрес</li>
        </ul>
      </div>
    </div>
  );
}
