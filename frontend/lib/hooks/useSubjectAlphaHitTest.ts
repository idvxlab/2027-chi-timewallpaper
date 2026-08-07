/**
 * useSubjectAlphaHitTest — off-screen Canvas alpha hit-test for the lifted
 * cutout PNG.
 *
 * Problem
 * -------
 * The lifted cutout PNG has a transparent background.  If we use the
 * bbox rectangle as the click target, the entire dead-air around the figure
 * would also be clickable.  We need to check whether the user clicked
 * inside a real opaque pixel of the subject.
 *
 * Solution
 * --------
 * 1. When a cutout URL arrives, draw the PNG into a small off-screen Canvas.
 * 2. Read back the pixel data once.
 * 3. Cache it keyed by cutoutUrl so repeated lifts of the same URL never
 *    re-download or re-draw.
 * 4. For hit-testing, map the on-screen pointer coordinates → canvas-local
 *    pixel coords → read the alpha at that pixel.
 * 5. Accept if alpha > ALPHA_HIT_THRESHOLD (24 / 255 ≈ 9%).
 *
 * Tolerance for elderly users
 * ---------------------------
 * We also check a small neighbourhood (±3 px in each direction).  If any
 * pixel in that 7×7 neighbourhood has alpha above the threshold, the hit
 * is valid.  This makes it easier to tap the edge of clothing / hair.
 *
 * Performance
 * -----------
 * - One Image → Canvas → getImageData per unique cutout URL.
 * - Hit-testing itself is a single array index read: O(1).
 * - The canvas is off-screen and never rendered to the DOM.
 * - Cache is a Map, cleared only by explicit _resetCache() (tests) or
 *   when the app unmounts.
 */

export type AlphaHitResult =
  | { hit: false }
  | { hit: true; alpha: number };

const ALPHA_HIT_THRESHOLD = 24;
const NEIGHBOURHOOD_RADIUS = 3;

type AlphaData = {
  /** Width of the canvas the PNG was drawn at. */
  width: number;
  /** Height of the canvas the PNG was drawn at. */
  height: number;
  /** Flat Uint8ClampedArray of RGBA pixel data. index = y*width*4 + x*4. */
  pixels: Uint8ClampedArray;
  /** The natural width of the source PNG (for coord mapping). */
  naturalWidth: number;
  naturalHeight: number;
};

const _cache = new Map<string, AlphaData>();
const _loading = new Map<string, Promise<AlphaData>>();

/** Returns the alpha value at a canvas-local pixel coordinate, or 0 if out of bounds. */
function sampleAlpha(data: AlphaData, cx: number, cy: number): number {
  const ix = Math.floor(cx);
  const iy = Math.floor(cy);
  if (ix < 0 || iy < 0 || ix >= data.width || iy >= data.height) return 0;
  return data.pixels[iy * data.width * 4 + ix * 4 + 3]; // +3 = alpha channel
}

/**
 * Synchronously reads cached alpha data. Returns null if not yet loaded.
 * Use loadAlphaData for async loading.
 */
export function getCachedAlphaData(url: string): AlphaData | null {
  return _cache.get(url) ?? null;
}

/**
 * Test whether a canvas-local pixel (cx, cy) is inside a real subject pixel.
 * Uses a small neighbourhood sample to be more forgiving for elderly users.
 */
export function testAlphaHit(data: AlphaData, cx: number, cy: number): AlphaHitResult {
  // Quick single-pixel check first.
  const quick = sampleAlpha(data, cx, cy);
  if (quick > ALPHA_HIT_THRESHOLD) return { hit: true, alpha: quick };

  // Tolerance pass: scan a ±3 px neighbourhood.
  for (let dy = -NEIGHBOURHOOD_RADIUS; dy <= NEIGHBOURHOOD_RADIUS; dy++) {
    for (let dx = -NEIGHBOURHOOD_RADIUS; dx <= NEIGHBOURHOOD_RADIUS; dx++) {
      if (dx === 0 && dy === 0) continue;
      const a = sampleAlpha(data, cx + dx, cy + dy);
      if (a > ALPHA_HIT_THRESHOLD) return { hit: true, alpha: a };
    }
  }
  return { hit: false };
}

/**
 * Build a small off-screen canvas, draw the PNG, and extract alpha data.
 * Returns cached data if the URL has already been processed.
 *
 * The canvas is sized to the PNG's natural dimensions.  Callers map
 * on-screen pointer coords → canvas coords before calling testAlphaHit.
 */
export async function loadAlphaData(url: string): Promise<AlphaData> {
  // Return cached data synchronously if ready.
  const cached = _cache.get(url);
  if (cached) return cached;

  // Deduplicate in-flight loads.
  const inFlight = _loading.get(url);
  if (inFlight) return inFlight;

  const promise = _loadAlphaData(url);
  _loading.set(url, promise);
  try {
    const data = await promise;
    _cache.set(url, data);
    return data;
  } finally {
    _loading.delete(url);
  }
}

async function _loadAlphaData(url: string): Promise<AlphaData> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        // Use a canvas sized to the PNG's natural dimensions.
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          reject(new Error("Could not get 2D canvas context"));
          return;
        }
        ctx.drawImage(img, 0, 0, img.naturalWidth, img.naturalHeight);
        const { data, width, height } = ctx.getImageData(
          0,
          0,
          img.naturalWidth,
          img.naturalHeight,
        );
        resolve({
          width,
          height,
          pixels: data,
          naturalWidth: img.naturalWidth,
          naturalHeight: img.naturalHeight,
        });
      } catch (err) {
        reject(err);
      }
    };
    img.onerror = () => reject(new Error(`Failed to load PNG for alpha hit-test: ${url}`));
    img.src = url;
  });
}

/**
 * Map an on-screen pointer position (clientX/Y) to a canvas-local pixel
 * coordinate for the given subject's bounding box.
 *
 * This is the inverse of imageBBoxToClientRect but maps into the
 * cutout PNG's natural coordinate space (not the full wallpaper).
 *
 * @param clientX, clientY — pointer position in viewport pixels.
 * @param bboxScreen — the on-screen rect of the lifted cutout
 *                     (as returned by imageBBoxToClientRect).
 * @param naturalWidth, naturalHeight — natural dimensions of the cutout PNG.
 */
export function clientToAlphaCanvasPoint(
  clientX: number,
  clientY: number,
  bboxScreen: { left: number; top: number; width: number; height: number },
  naturalWidth: number,
  naturalHeight: number,
): { cx: number; cy: number } {
  const localX = clientX - bboxScreen.left;
  const localY = clientY - bboxScreen.top;
  // Map from screen bbox pixels → natural PNG pixels.
  const cx = (localX / bboxScreen.width) * naturalWidth;
  const cy = (localY / bboxScreen.height) * naturalHeight;
  return { cx, cy };
}

/** Test-only: clear all caches. */
export function _resetAlphaHitCache(): void {
  _cache.clear();
  _loading.clear();
}
