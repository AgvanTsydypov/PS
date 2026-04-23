import type {
  Dispatch,
  MouseEvent,
  MutableRefObject,
  SetStateAction,
} from "react";

export function isSafeExternalUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}

const MAX_TILT = 20;
const PROXIMITY_PX = 10;
const FLIP_DURATION_MS = 720;
const CENTER_ZONE_MIN = 0.25;
const CENTER_ZONE_MAX = 0.75;
const HOVER_CLICK_GRACE_MS = 260;
const PRESS_CLICK_GRACE_MS = 900;

type RecordStateSetter = Dispatch<SetStateAction<Record<string, boolean>>>;

function getInteractiveWrapper(target: HTMLElement): HTMLElement | null {
  return target.closest<HTMLElement>(".nft-card-wrapper, .card-ticker-item") ?? target.parentElement;
}

function isCenterZone(relativeX: number, relativeY: number): boolean {
  return (
    relativeX >= CENTER_ZONE_MIN &&
    relativeX <= CENTER_ZONE_MAX &&
    relativeY >= CENTER_ZONE_MIN &&
    relativeY <= CENTER_ZONE_MAX
  );
}

function isCenterZoneByWrapperCoordinates(
  target: HTMLElement,
  clientX: number,
  clientY: number,
): boolean {
  const wrapper = getInteractiveWrapper(target);
  const rect = wrapper?.getBoundingClientRect();
  if (!rect || !rect.width || !rect.height) return false;
  const relativeX = (clientX - rect.left) / rect.width;
  const relativeY = (clientY - rect.top) / rect.height;
  return isCenterZone(relativeX, relativeY);
}

function isHoverActiveForClick(target: HTMLElement): boolean {
  if (target.dataset.tiltActive === "1") return true;
  const lastTiltAt = Number(target.dataset.lastTiltAt ?? 0);
  if (!Number.isFinite(lastTiltAt) || lastTiltAt <= 0) return false;
  return Date.now() - lastTiltAt <= HOVER_CLICK_GRACE_MS;
}

function hasCenterPressForClick(target: HTMLElement): boolean {
  if (target.dataset.pressInCenter !== "1") return false;
  const pressAt = Number(target.dataset.pressStartedAt ?? 0);
  if (!Number.isFinite(pressAt) || pressAt <= 0) return false;
  return Date.now() - pressAt <= PRESS_CLICK_GRACE_MS;
}

export function markCardPressStart(target: HTMLElement, clientX: number, clientY: number): void {
  const inCenterZone = isCenterZoneByWrapperCoordinates(target, clientX, clientY);
  target.dataset.pressInCenter = inCenterZone ? "1" : "0";
  target.dataset.pressStartedAt = String(Date.now());
}

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
  const canNavigateFromCenter = target.dataset.centerNavigate === "1";
  const inCenterZone = isCenterZone(relativeX, relativeY);
  const wrapper = getInteractiveWrapper(target);

  target.classList.add("nft-card-active");
  target.parentElement?.classList.add("nft-card-wrapper-active");
  if (wrapper) wrapper.dataset.centerZoneActive = canNavigateFromCenter && inCenterZone ? "1" : "0";
  target.dataset.tiltActive = "1";
  target.dataset.lastTiltAt = String(Date.now());
  target.dataset.pointerRelX = relativeX.toFixed(4);
  target.dataset.pointerRelY = relativeY.toFixed(4);
  target.dataset.pointerInCenter = inCenterZone ? "1" : "0";
  target.style.setProperty("--nft-tilt-x", `${rotateX.toFixed(2)}deg`);
  target.style.setProperty("--nft-tilt-y", `${rotateY.toFixed(2)}deg`);
  target.style.setProperty("--pointer-x", `${(relativeX * 100).toFixed(2)}%`);
  target.style.setProperty("--pointer-y", `${(relativeY * 100).toFixed(2)}%`);
  target.style.setProperty("--tilt-ratio-x", tiltRatioX.toFixed(3));
  target.style.setProperty("--tilt-ratio-y", tiltRatioY.toFixed(3));
  if (canNavigateFromCenter) {
    target.style.cursor = inCenterZone ? "pointer" : "grab";
  } else {
    target.style.cursor = "";
  }
}

export function resetCardTilt(target: HTMLElement): void {
  const wrapper = getInteractiveWrapper(target);
  target.classList.remove("nft-card-active");
  target.parentElement?.classList.remove("nft-card-wrapper-active");
  if (wrapper) wrapper.dataset.centerZoneActive = "0";
  target.dataset.tiltActive = "0";
  target.dataset.pointerInCenter = "0";
  delete target.dataset.pointerRelX;
  delete target.dataset.pointerRelY;
  delete target.dataset.pressInCenter;
  delete target.dataset.pressStartedAt;
  target.style.setProperty("--nft-tilt-x", "0deg");
  target.style.setProperty("--nft-tilt-y", "0deg");
  target.style.setProperty("--pointer-x", "50%");
  target.style.setProperty("--pointer-y", "50%");
  target.style.setProperty("--tilt-ratio-x", "0");
  target.style.setProperty("--tilt-ratio-y", "0");
  target.style.cursor = "";
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

export function navigateToCardIfCenterClick(
  target: HTMLElement,
  slug: string,
  clientX?: number,
  clientY?: number,
  options?: { basePath?: string },
): boolean {
  if (!slug || target.dataset.centerNavigate !== "1") return false;
  if (!isHoverActiveForClick(target)) return false;
  const pointerInCenterByPress = hasCenterPressForClick(target);
  const pointerInCenterByMove = target.dataset.pointerInCenter === "1";
  const pointerInCenterByClick =
    typeof clientX === "number" &&
    typeof clientY === "number" &&
    isCenterZoneByWrapperCoordinates(target, clientX, clientY);
  if (!pointerInCenterByPress && !pointerInCenterByMove && !pointerInCenterByClick) return false;
  // Two permalinks: ``/cards/{slug}`` for minted STARs (claims-backed) and
  // ``/preview/{slug}`` for live showcase previews (preview_cards).
  // Callers whose card is a preview must pass ``basePath: "/preview"``.
  const base = (options?.basePath ?? "/cards").replace(/\/+$/, "");
  const targetPath = `${base}/${encodeURIComponent(slug)}`;
  const resolved = new URL(targetPath, window.location.origin);
  if (resolved.origin !== window.location.origin) return false;
  window.location.href = resolved.pathname;
  return true;
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
