/**
 * Convert a browser viewport pointer position into the source image's
 * natural pixel / normalized coordinate space.
 *
 * The wallpaper is rendered as a CSS `background-image` with
 * `background-size: cover` and `background-position: center`. With cover,
 * the image fills the container while preserving aspect ratio; the
 * container crops one axis. To map a viewport pointer back into the image
 * we have to undo that crop.
 *
 * Reference: CSS background-size cover math.
 *   scale     = max(containerW / naturalW, containerH / naturalH)
 *   renderedW = naturalW * scale
 *   renderedH = naturalH * scale
 *   offsetX   = (containerW - renderedW) / 2
 *   offsetY   = (containerH - renderedH) / 2
 *   imageX    = (clientX - rect.left - offsetX) / scale
 *   imageY    = (clientY - rect.top  - offsetY) / scale
 *
 * If both axes come out positive and within the natural bounds, the
 * pointer is over the image. Otherwise it's outside the image area and
 * the caller should treat it as a miss.
 */

export type ClientPointToImageParams = {
  clientX: number;
  clientY: number;
  /** DOMRect of the wallpaper display container. */
  imageRect: DOMRect;
  naturalWidth: number;
  naturalHeight: number;
  objectFit?: "cover" | "contain";
  /** 0 = left crop, 0.5 = centered crop, 1 = right crop. */
  objectPositionX?: number;
};

export type ImagePoint = {
  imageX: number;
  imageY: number;
  normalizedX: number;
  normalizedY: number;
  inside: boolean;
};

export function clientPointToImagePoint(
  params: ClientPointToImageParams,
): ImagePoint {
  const { clientX, clientY, imageRect, naturalWidth, naturalHeight } = params;
  const objectFit = params.objectFit ?? "cover";
  const objectPositionX = Math.min(
    1,
    Math.max(0, params.objectPositionX ?? 0.5),
  );

  const containerW = imageRect.width;
  const containerH = imageRect.height;

  if (
    containerW <= 0 ||
    containerH <= 0 ||
    naturalWidth <= 0 ||
    naturalHeight <= 0
  ) {
    return { imageX: 0, imageY: 0, normalizedX: 0, normalizedY: 0, inside: false };
  }

  const localX = clientX - imageRect.left;
  const localY = clientY - imageRect.top;

  let scale: number;
  if (objectFit === "contain") {
    scale = Math.min(containerW / naturalWidth, containerH / naturalHeight);
  } else {
    scale = Math.max(containerW / naturalWidth, containerH / naturalHeight);
  }

  const renderedW = naturalWidth * scale;
  const renderedH = naturalHeight * scale;
  const offsetX = (containerW - renderedW) * objectPositionX;
  const offsetY = (containerH - renderedH) / 2;

  const imageX = (localX - offsetX) / scale;
  const imageY = (localY - offsetY) / scale;

  const inside =
    imageX >= 0 &&
    imageY >= 0 &&
    imageX <= naturalWidth &&
    imageY <= naturalHeight;

  return {
    imageX,
    imageY,
    normalizedX: imageX / naturalWidth,
    normalizedY: imageY / naturalHeight,
    inside,
  };
}

/**
 * Hit-test a normalized pointer against the two fixed subject regions of
 * the final wallpaper.
 *
 * FIXED shared wallpaper layout (Phase shared_wallpaper):
 *   upper (upper-right, x≈52-100%, y≈2-45%): child / young person
 *   lower (lower-left, x≈0-55%, y≈42-100%): elder / older person
 *
 * The two rectangles are designed to overlap slightly along the diagonal so
 * a diagonal press is unambiguous; a press exactly on the diagonal would be
 * ambiguous, so we pick upper in that tie case (y <= 0.45 wins).
 *
 * Region containment (not nearest) is used on purpose: if a press misses
 * both regions entirely (e.g. landscape only) the result is null and we
 * do NOT call the backend. This prevents the "press the elder, extract
 * the young" failure mode that nearest-subject selection tends to produce.
 */
export type SubjectHit = "upper" | "lower";

/**
 * When true, hitTest uses looser bounding rectangles so manual testing
 * on a desktop browser is easier (mouse can't easily land inside the
 * tight strict rectangles). Flip to false before shipping.
 */
export const DEBUG_SUBJECT_LIFT_LOOSE = false;

export function hitTestSubject(nx: number, ny: number): SubjectHit | null {
  // strict: tight around the figure outlines
  const strictUpper =
    nx >= 0.52 && nx <= 1.0 && ny >= 0.02 && ny <= 0.45;
  const strictLower =
    nx >= 0.0 && nx <= 0.55 && ny >= 0.42 && ny <= 1.0;
  // loose: wider, easier to land with a mouse
  const looseUpper =
    nx >= 0.45 && nx <= 1.0 && ny >= 0.0 && ny <= 0.5;
  const looseLower =
    nx >= 0.0 && nx <= 0.65 && ny >= 0.35 && ny <= 1.0;

  const up = DEBUG_SUBJECT_LIFT_LOOSE ? looseUpper : strictUpper;
  const lo = DEBUG_SUBJECT_LIFT_LOOSE ? looseLower : strictLower;

  if (up && lo) {
    // Tie: pick the region whose center is closer.
    const distUpper = Math.hypot(nx - 0.76, ny - 0.235);
    const distLower = Math.hypot(nx - 0.275, ny - 0.71);
    return distUpper <= distLower ? "upper" : "lower";
  }
  if (up) return "upper";
  if (lo) return "lower";
  return null;
}

/**
 * Convert a bbox in image-natural pixel coordinates to viewport-relative
 * display pixels, using the same `background-size: cover` geometry as the
 * wallpaper background image in AtmosphereLayer.
 *
 * This is the inverse of the math baked into CSS `background-position: center`
 * + `background-size: cover`:
 *
 *   scale     = max(containerW / naturalW, containerH / naturalH)
 *   renderedW = naturalW * scale
 *   renderedH = naturalH * scale
 *   offsetX   = (containerW - renderedW) / 2     // horizontal crop
 *   offsetY   = (containerH - renderedH) / 2     // vertical crop
 *   displayX  = containerRect.left + offsetX + bbox.x * scale
 *   displayY  = containerRect.top  + offsetY + bbox.y * scale
 *
 * The returned {left, top, width, height} are in viewport pixels and are
 * suitable for CSS `position: absolute` / `left` / `top` — the SubjectLiftLayer
 * overlay div is itself viewport-fixed, so this coordinate space matches.
 *
 * containerRect MUST come from getBoundingClientRect() (viewport-relative).
 */
export type ImageBBoxToClientRectParams = {
  bbox: { x: number; y: number; width: number; height: number };
  containerRect: DOMRect;
  naturalWidth: number;
  naturalHeight: number;
  /** 0 = left crop, 0.5 = centered crop, 1 = right crop. */
  objectPositionX?: number;
};

export function imageBBoxToClientRect(
  params: ImageBBoxToClientRectParams,
): { left: number; top: number; width: number; height: number } {
  const { bbox, containerRect, naturalWidth, naturalHeight } = params;
  const objectPositionX = Math.min(
    1,
    Math.max(0, params.objectPositionX ?? 0.5),
  );

  const containerW = containerRect.width;
  const containerH = containerRect.height;

  if (containerW <= 0 || containerH <= 0 || naturalWidth <= 0 || naturalHeight <= 0) {
    return { left: 0, top: 0, width: 0, height: 0 };
  }

  const scale = Math.max(containerW / naturalWidth, containerH / naturalHeight);
  const renderedW = naturalWidth * scale;
  const renderedH = naturalHeight * scale;

  // offset within the container where the image starts (cover = fill, crop excess)
  const offsetX = (containerW - renderedW) * objectPositionX;
  const offsetY = (containerH - renderedH) / 2;

  return {
    left: containerRect.left + offsetX + bbox.x * scale,
    top: containerRect.top + offsetY + bbox.y * scale,
    width: bbox.width * scale,
    height: bbox.height * scale,
  };
}

/**
 * Result of alpha hit-test.
 *   bboxHit: the pointer lies inside the bbox rectangle
 *   alphaHit: the pointer lies inside an opaque (alpha > threshold) pixel of the
 *             cutout image (only meaningful when cutoutUrl is provided AND the
 *             alpha map has been preloaded). When alpha map is unavailable,
 *             this falls back to bboxHit.
 */
export type AlphaHitResult = {
  bboxHit: boolean;
  alphaHit: boolean;
};

/**
 * Cheap alpha hit-test against the cutout image at the given viewport coords.
 *
 * Phase 3.5: To keep CPU cost minimal we cache a tiny alpha mask derived from
 * the cutout image. This implementation falls back to bbox-only hit-testing
 * when the mask is missing. Callers can pre-warm via `getOrBuildAlphaMask`.
 */
export type AlphaMask = {
  url: string;
  width: number;
  height: number;
  /** Uint8Array of length width*height. 1 = opaque enough, 0 = transparent. */
  pixels: Uint8Array;
  bbox: { x: number; y: number; width: number; height: number };
  containerRect: DOMRect;
  naturalWidth: number;
  naturalHeight: number;
};

const _alphaCache = new Map<string, AlphaMask>();
const ALPHA_CACHE_LIMIT = 8;

export function _evictAlphaCache() {
  _alphaCache.clear();
}

export function _setAlphaMask(mask: AlphaMask) {
  if (_alphaCache.has(mask.url)) {
    _alphaCache.delete(mask.url);
  }
  _alphaCache.set(mask.url, mask);
  while (_alphaCache.size > ALPHA_CACHE_LIMIT) {
    const first = _alphaCache.keys().next().value;
    if (first === undefined) break;
    _alphaCache.delete(first);
  }
}

export function _getAlphaMask(url: string): AlphaMask | undefined {
  return _alphaCache.get(url);
}

export function hitTestAlpha(
  clientX: number,
  clientY: number,
  displayed: {
    cutoutUrl: string;
    bbox: { x: number; y: number; width: number; height: number };
  },
  containerRect: DOMRect,
  naturalWidth: number,
  naturalHeight: number,
): AlphaHitResult {
  const screen = imageBBoxToClientRect({
    bbox: displayed.bbox,
    containerRect,
    naturalWidth,
    naturalHeight,
  });
  const bboxHit =
    clientX >= screen.left &&
    clientX <= screen.left + screen.width &&
    clientY >= screen.top &&
    clientY <= screen.top + screen.height;
  if (!bboxHit) return { bboxHit: false, alphaHit: false };

  const mask = _alphaCache.get(displayed.cutoutUrl);
  if (!mask || mask.width === 0 || mask.height === 0) {
    // Fallback: only bbox
    return { bboxHit: true, alphaHit: true };
  }

  const localX = clientX - screen.left;
  const localY = clientY - screen.top;
  const px = Math.floor((localX / screen.width) * mask.width);
  const py = Math.floor((localY / screen.height) * mask.height);
  if (px < 0 || py < 0 || px >= mask.width || py >= mask.height) {
    return { bboxHit: true, alphaHit: true };
  }
  const alphaHit = mask.pixels[py * mask.width + px] === 1;
  return { bboxHit: true, alphaHit };
}

/**
 * Role and Region Type Definitions
 *
 * Shared wallpaper fixed layout (Phase shared_wallpaper):
 *   upper = upper-right region = child/young person
 *   lower = lower-left region = elder/older person
 *
 * These are FIXED — they never change based on viewerRole.
 */

// Viewer role: who is currently looking at the wallpaper
export type ViewerRole = "elder" | "child";

// Person identity in the wallpaper: the actual family role
export type PersonRole = "elder" | "child";

// Subject region: position in the wallpaper image
export type SubjectRegion = "upper" | "lower";

/**
 * Get the person identity (family role) for a given region in the wallpaper.
 *
 * This is FIXED — the wallpaper always has:
 *   upper (upper-right) = child
 *   lower (lower-left) = elder
 *
 * This never changes regardless of who is viewing.
 */
export function getPersonRoleByRegion(region: SubjectRegion): PersonRole {
  return region === "upper" ? "child" : "elder";
}

/**
 * Get the partner's family role for a given viewer.
 *
 * The partner is the other person in the relationship:
 *   elder viewer → partner is child
 *   child viewer → partner is elder
 */
export function getPartnerRole(viewerRole: ViewerRole): PersonRole {
  return viewerRole === "elder" ? "child" : "elder";
}

/**
 * Get the region where the partner is located.
 *
 *   elder viewer → partner (child) is in upper region
 *   child viewer → partner (elder) is in lower region
 */
export function getPartnerRegion(viewerRole: ViewerRole): SubjectRegion {
  return viewerRole === "elder" ? "upper" : "lower";
}

/**
 * Check if a given region is allowed to be lifted for a viewer.
 *
 * Only the partner's region is liftable. The viewer's own region
 * (self) cannot be lifted.
 *
 * Returns true if:
 *   - candidate region equals partner region, AND
 *   - the person in that region equals the partner role
 */
export function isRegionLiftingAllowed(
  viewerRole: ViewerRole,
  candidateRegion: SubjectRegion,
): boolean {
  const personRole = getPersonRoleByRegion(candidateRegion);
  const partnerRole = getPartnerRole(viewerRole);
  const partnerRegion = getPartnerRegion(viewerRole);

  return candidateRegion === partnerRegion && personRole === partnerRole;
}

/**
 * DEPRECATED: Use getPersonRoleByRegion instead.
 *
 * This function existed before but returned "young" instead of "child".
 * The type PersonRole now uses "child" instead of "young".
 */
export type LiftedPersonRole = "young" | "elder";

export function getLiftedPersonRole(
  _viewerRole: "child" | "elder",
  region: SubjectHit,
): LiftedPersonRole {
  // Phase shared_wallpaper: fixed layout, no longer viewer-dependent.
  // upper = child/young, lower = elder — always.
  // Note: This returns "young" for backward compatibility.
  // New code should use getPersonRoleByRegion which returns "child".
  return region === "upper" ? "young" : "elder";
}
