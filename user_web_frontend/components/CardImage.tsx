"use client";

import React, { useEffect, useRef, useState } from "react";

type CardImageProps = Omit<React.ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  /** Primary image URL (R2 mirror — CDN-cached, no per-IP rate limit). */
  src: string;
  /** Optional fallback (public Pinata gateway) used once if the primary
   *  fails to load. Lets a rare R2 miss still resolve via IPFS. */
  fallbackSrc?: string | null;
};

/**
 * Card <img> that serves the R2-mirror URL first and transparently retries the
 * Pinata gateway on error. The primary URL is CDN-fronted and immutable-cached,
 * so it never 429s the way hot-linking gateway.pinata.cloud does; the fallback
 * only kicks in if R2 itself returns an error for a given asset.
 */
export default function CardImage({ src, fallbackSrc, onError, ...rest }: CardImageProps) {
  const [currentSrc, setCurrentSrc] = useState(src);
  const triedFallback = useRef(false);

  // Reset when the primary URL changes (e.g. a different card renders into the
  // same node) so the new image gets its own fallback budget.
  useEffect(() => {
    setCurrentSrc(src);
    triedFallback.current = false;
  }, [src]);

  return (
    // Raw <img> by design (matches the rest of the card UI); alt comes via ...rest.
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
    <img
      {...rest}
      src={currentSrc}
      onError={(event) => {
        if (
          !triedFallback.current &&
          fallbackSrc &&
          fallbackSrc !== currentSrc
        ) {
          triedFallback.current = true;
          setCurrentSrc(fallbackSrc);
        }
        onError?.(event);
      }}
    />
  );
}
