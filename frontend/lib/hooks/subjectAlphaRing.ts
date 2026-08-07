/**
 * Build an inline SVG markup string that draws a thin white ring around the
 * non-transparent pixels of an arbitrary image — and an animated highlight
 * sweeping around the same ring.
 *
 * How it works
 * ------------
 * 1. An inline `<image>` element references the cutout PNG (which has real
 *    alpha). The image is rendered inside an SVG sized to match the on-screen
 *    subject bounding box.
 * 2. An SVG `<filter>` chains:
 *
 *      feMorphology(dilate, r=2)   — grow the alpha by N pixels
 *      feComposite(out)            — subtract the original alpha
 *                                   → leaves only the outer ring pixels
 *      feGaussianBlur(stdDeviation=0.6) — soften the edge
 *
 *    The result is a clean 2-px-wide outline that follows every alpha edge:
 *    hair, clothing folds, arms, body holes — no rectangular border.
 * 3. A second pass with a smaller dilate + brighter fill produces the
 *    highlight ring, which is animated via a CSS `stroke-dashoffset`
 *    sweep around the bounding box.
 * 4. The filtered ring + image are stacked in the SVG. The host component
 *    layers them on top of the wallpaper using `imageBBoxToClientRect`.
 *
 * Browser compatibility
 * ---------------------
 * - Chrome / Edge / Safari / Firefox all support `feMorphology` and
 *   `feComposite operator="out"` on inline `<image>` elements.
 * - No external dependencies, no AI, no matting. The PNG itself is the
 *   source of truth for what counts as "subject pixel".
 *
 * Caching
 * -------
 * The SVG markup is generated once per PNG URL. The host component caches
 * the markup in a Map keyed by URL so re-renders never re-allocate filter
 * primitives. See `getAlphaRingMarkup`.
 */

export type AlphaRingOptions = {
  /** Width of the visible outer ring (CSS pixels on screen). */
  ringWidth?: number;
  /** Opacity of the static white ring. 0..1. */
  ringOpacity?: number;
  /** Highlight color (CSS color). */
  highlightColor?: string;
  /** Softness of the alpha edge (Gaussian stdDeviation). */
  blurStdDeviation?: number;
  /** Whether to include an animated sweeping highlight. */
  shimmer?: boolean;
};

const DEFAULTS: Required<AlphaRingOptions> = {
  ringWidth: 2,
  ringOpacity: 0.7,
  highlightColor: "rgba(255,255,255,1)",
  blurStdDeviation: 0.6,
  shimmer: true,
};

/**
 * Generate a unique filter id per call so multiple SVG stacks on the same
 * page don't share a filter. Filter id is allowed to contain letters /
 * digits / hyphens only — we sanitize the URL and include width/height
 * so the same PNG lifted into two different bboxes gets distinct
 * filter chains (no cross-talk).
 */
export function makeFilterId(
  cutoutUrl: string,
  width: number,
  height: number,
): string {
  const cleaned = cutoutUrl
    .replace(/[^a-zA-Z0-9_-]/g, "")
    .slice(-24);
  return `subj-ring-${cleaned}-${width}x${height}`;
}

/**
 * Build the inner SVG markup (everything that goes inside the host
 * `<svg>` element). The host owns the `<svg>` tag with width/height/x/y
 * because those depend on the bbox mapping.
 */
export function buildAlphaRingInnerMarkup(
  cutoutUrl: string,
  width: number,
  height: number,
  options?: AlphaRingOptions,
): { markup: string; filterId: string } {
  const opts = { ...DEFAULTS, ...(options || {}) };
  const filterId = makeFilterId(cutoutUrl, width, height);

  // The morphology radius needs to scale with display size. We dilate by
  // ringWidth pixels in the *original* alpha space, but here the inline
  // image is rendered at display size, so the dilate radius is in display
  // pixels.
  const r = Math.max(1, Math.round(opts.ringWidth));
  const stdDev = Math.max(0, opts.blurStdDeviation);

  // Build the filter definitions.
  //   base    — static white ring (opacity 0.7)
  //   shimmer — brighter ring whose opacity is animated via CSS to sweep
  //             a highlight across the silhouette
  const filterDef = `
    <defs>
      <filter id="${filterId}-base" x="-10%" y="-10%" width="120%" height="120%">
        <feMorphology in="SourceAlpha" operator="dilate" radius="${r}" result="ring-outer"/>
        <feComposite in="ring-outer" in2="SourceAlpha" operator="out" result="ring-only"/>
        <feGaussianBlur in="ring-only" stdDeviation="${stdDev}" result="ring-soft"/>
        <feFlood flood-color="white" flood-opacity="${opts.ringOpacity}" result="ring-fill"/>
        <feComposite in="ring-fill" in2="ring-soft" operator="in" result="ring-painted"/>
      </filter>
      ${
        opts.shimmer
          ? `<filter id="${filterId}-shimmer" x="-10%" y="-10%" width="120%" height="120%">
        <feMorphology in="SourceAlpha" operator="dilate" radius="${Math.max(1, r - 1)}" result="shim-outer"/>
        <feComposite in="shim-outer" in2="SourceAlpha" operator="out" result="shim-only"/>
        <feGaussianBlur in="shim-only" stdDeviation="${stdDev + 0.4}" result="shim-soft"/>
        <feFlood flood-color="white" flood-opacity="0.95" result="shim-fill"/>
        <feComposite in="shim-fill" in2="shim-soft" operator="in" result="shim-painted"/>
      </filter>`
          : ""
      }
    </defs>
  `;

  // Layer order inside the SVG, painted bottom-to-top:
  //   1. The cutout PNG itself (no filter) — this is the figure.
  //   2. Base white outline (filtered dilate-minus-alpha).
  //   3. Shimmer ring (brighter, animated opacity).
  //
  // All three reference the SAME URL, so the browser's HTTP cache
  // serves them from one download.  The image element is reused across
  // layers via <use href="#cutout-{filterId}"/>.
  const safeUrl = escapeAttr(cutoutUrl);
  const imageId = `cutout-${filterId}`;
  const imageMarkup = `<image id="${imageId}" href="${safeUrl}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="none"/>`;

  const baseMarkup = `<use href="#${imageId}" filter="url(#${filterId}-base)"/>`;
  const shimmerMarkup = opts.shimmer
    ? `<use class="subject-lift-shimmer" href="#${imageId}" filter="url(#${filterId}-shimmer)"/>`
    : "";

  return {
    markup: filterDef + imageMarkup + baseMarkup + shimmerMarkup,
    filterId,
  };
}

/** Escape a value for use inside an SVG attribute (double-quoted). */
function escapeAttr(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Per-URL cache for the inner markup. Hosts can call `getAlphaRingMarkup`
 * with a stable cutoutUrl and get back the same markup object without
 * re-running the SVG filter compilation.
 */
const _cache = new Map<string, { markup: string; filterId: string }>();

export function getAlphaRingMarkup(
  cutoutUrl: string,
  width: number,
  height: number,
  options?: AlphaRingOptions,
): { markup: string; filterId: string } {
  // Cache key includes width/height because the inner `<image>` is sized
  // to bbox. Two lifts of the same PNG with different bbox sizes must
  // get fresh markup.
  const key = `${cutoutUrl}|${width}|${height}|${JSON.stringify(options || {})}`;
  const cached = _cache.get(key);
  if (cached) return cached;
  const fresh = buildAlphaRingInnerMarkup(cutoutUrl, width, height, options);
  _cache.set(key, fresh);
  return fresh;
}

/** Test-only: clear the alpha-ring cache. */
export function _resetAlphaRingCache(): void {
  _cache.clear();
}