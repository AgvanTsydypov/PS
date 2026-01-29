import { formatAddress } from '@/lib/utils';
import { useState } from 'react';

interface AddressDisplayProps {
  address: string;
  label?: string;
  showCopy?: boolean;
  showFull?: boolean;
}

/**
 * Компонент для отображения Ethereum адреса с возможностью копирования
 */
export function AddressDisplay({
  address,
  label,
  showCopy = true,
  showFull = false,
}: AddressDisplayProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const displayAddress = showFull ? address : formatAddress(address);

  return (
    <div className="space-y-1">
      {label && (
        <p className="text-sm text-gray-600 font-medium">{label}</p>
      )}
      
      <div className="flex items-center gap-2">
        <code className="text-sm bg-gray-100 px-3 py-1 rounded font-mono">
          {displayAddress}
        </code>
        
        {showCopy && (
          <button
            onClick={handleCopy}
            className="text-xs bg-blue-500 hover:bg-blue-600 text-white px-2 py-1 rounded transition"
            title="Копировать адрес"
          >
            {copied ? '✓ Скопировано' : 'Копировать'}
          </button>
        )}
      </div>
      
      {!showFull && (
        <p className="text-xs text-gray-500">
          Полный адрес: {address}
        </p>
      )}
    </div>
  );
}
