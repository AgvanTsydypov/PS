import type { Config } from "dompurify";

/** Shared DOMPurify options for generated card SVG (server + client). */
export const CARD_SVG_PURIFY_CONFIG: Config = {
  USE_PROFILES: { svg: true, svgFilters: true },
};
