export type WallpaperDisplayGeometry = {
  rect: DOMRect;
  objectPositionX: number;
};

function parsePositionX(value: string | undefined): number | null {
  if (!value) return null;
  const normalized = value.trim().toLowerCase();
  if (normalized === "left") return 0;
  if (normalized === "center") return 0.5;
  if (normalized === "right") return 1;
  if (!normalized.endsWith("%")) return null;
  const numeric = Number.parseFloat(normalized);
  if (!Number.isFinite(numeric)) return null;
  return Math.min(1, Math.max(0, numeric / 100));
}

/**
 * Read the wallpaper's live display rectangle and horizontal crop position.
 * This remains accurate while background-position is transitioning between
 * the left, center and right panorama views.
 */
export function getWallpaperDisplayGeometry(): WallpaperDisplayGeometry | null {
  const motionSurface = document.querySelector<HTMLElement>(
    "[data-wallpaper-motion-surface]",
  );
  const fallback = document.querySelector<HTMLElement>(
    "[data-subject-lift-container]",
  );
  const rect =
    motionSurface?.getBoundingClientRect() ?? fallback?.getBoundingClientRect();
  if (!rect) return null;

  const wallpaperImage = document.querySelector<HTMLElement>(
    "[data-wallpaper-image]",
  );
  const computedPosition = wallpaperImage
    ? window.getComputedStyle(wallpaperImage).backgroundPositionX
    : undefined;
  const fallbackPosition = motionSurface?.dataset.wallpaperPositionX;

  return {
    rect,
    objectPositionX:
      parsePositionX(computedPosition) ??
      parsePositionX(fallbackPosition) ??
      0.5,
  };
}
