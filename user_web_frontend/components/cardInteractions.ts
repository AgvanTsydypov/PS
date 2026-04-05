import type {
  Dispatch,
  MouseEvent,
  MutableRefObject,
  SetStateAction,
} from "react";

const MAX_TILT = 20;
const PROXIMITY_PX = 10;
const FLIP_DURATION_MS = 720;

type RecordStateSetter = Dispatch<SetStateAction<Record<string, boolean>>>;

export function updateCardTilt(target: HTMLElement, clientX: number, clientY: number): void {
  const rect = target.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const relativeX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  const relativeY = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
  const rotateY = (relativeX - 0.5) * (MAX_TILT * 2);
  const rotateX = (0.5 - relativeY) * (MAX_TILT * 2);
  // Plain-number ratios (-1..1) for CSS diffuse lighting.
  // +ratio-x -> tilted down (shadow), -ratio-x -> tilted up (light).
  const tiltRatioX = (relativeY - 0.5) * 2;
  const tiltRatioY = (relativeX - 0.5) * 2;

  target.classList.add("nft-card-active");
  target.parentElement?.classList.add("nft-card-wrapper-active");
  target.style.setProperty("--nft-tilt-x", `${rotateX.toFixed(2)}deg`);
  target.style.setProperty("--nft-tilt-y", `${rotateY.toFixed(2)}deg`);
  target.style.setProperty("--pointer-x", `${(relativeX * 100).toFixed(2)}%`);
  target.style.setProperty("--pointer-y", `${(relativeY * 100).toFixed(2)}%`);
  target.style.setProperty("--tilt-ratio-x", tiltRatioX.toFixed(3));
  target.style.setProperty("--tilt-ratio-y", tiltRatioY.toFixed(3));
}

export function resetCardTilt(target: HTMLElement): void {
  target.classList.remove("nft-card-active");
  target.parentElement?.classList.remove("nft-card-wrapper-active");
  target.style.setProperty("--nft-tilt-x", "0deg");
  target.style.setProperty("--nft-tilt-y", "0deg");
  target.style.setProperty("--pointer-x", "50%");
  target.style.setProperty("--pointer-y", "50%");
  target.style.setProperty("--tilt-ratio-x", "0");
  target.style.setProperty("--tilt-ratio-y", "0");
}

export function handleCardGridMouseMove(
  event: MouseEvent<HTMLDivElement>,
  {
    wrapperSelector,
    cardSelector,
    flippingClassName = "generated-card-preview-card-flipping",
  }: {
    wrapperSelector: string;
    cardSelector: string;
    flippingClassName?: string;
  },
): void {
  const clientX = event.clientX;
  const clientY = event.clientY;
  const wrappers = event.currentTarget.querySelectorAll<HTMLElement>(wrapperSelector);

  wrappers.forEach((wrapper) => {
    const card = wrapper.querySelector<HTMLElement>(cardSelector);
    if (!card) return;
    if (card.classList.contains(flippingClassName)) {
      resetCardTilt(card);
      return;
    }
    // Use transformed card bounds so scaled edges remain interactive.
    const rect = card.getBoundingClientRect();
    const isWithinProximity =
      clientX >= rect.left - PROXIMITY_PX &&
      clientX <= rect.right + PROXIMITY_PX &&
      clientY >= rect.top - PROXIMITY_PX &&
      clientY <= rect.bottom + PROXIMITY_PX;

    if (!isWithinProximity) {
      resetCardTilt(card);
      return;
    }

    const clampedX = Math.min(rect.right, Math.max(rect.left, clientX));
    const clampedY = Math.min(rect.bottom, Math.max(rect.top, clientY));
    updateCardTilt(card, clampedX, clampedY);
  });
}

export function handleCardGridMouseLeave(
  event: MouseEvent<HTMLDivElement>,
  cardSelector: string,
): void {
  const cards = event.currentTarget.querySelectorAll<HTMLElement>(cardSelector);
  cards.forEach((card) => resetCardTilt(card));
}

export function triggerCardFlip(
  id: string,
  target: HTMLElement,
  flipTimerRef: MutableRefObject<Record<string, number | null>>,
  setAnimating: RecordStateSetter,
  setFlipped: RecordStateSetter,
): void {
  resetCardTilt(target);
  const existingTimer = flipTimerRef.current[id];
  if (existingTimer) window.clearTimeout(existingTimer);
  setAnimating((prev) => ({
    ...prev,
    [id]: true,
  }));
  setFlipped((prev) => ({
    ...prev,
    [id]: !prev[id],
  }));
  flipTimerRef.current[id] = window.setTimeout(() => {
    setAnimating((prev) => ({
      ...prev,
      [id]: false,
    }));
    flipTimerRef.current[id] = null;
  }, FLIP_DURATION_MS);
}

export function clearFlipTimers(
  flipTimerRef: MutableRefObject<Record<string, number | null>>,
): void {
  Object.values(flipTimerRef.current).forEach((timerId) => {
    if (timerId) window.clearTimeout(timerId);
  });
}
