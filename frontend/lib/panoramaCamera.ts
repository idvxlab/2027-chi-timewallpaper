export type PanoramaDirection = "left" | "right";

export const TODAY_PANORAMA_PAGE_COUNT = 4;

/** Whether an index belongs to the newest panorama-enabled pages for today. */
export function isRecentTodayPanoramaIndex(
  index: number,
  total: number,
  count = TODAY_PANORAMA_PAGE_COUNT,
): boolean {
  if (total <= 0 || index < 0 || index >= total || count <= 0) return false;
  return index >= Math.max(0, total - count);
}

export const PANORAMA_HOLD_DELAY_MS = 1_000;
export const PANORAMA_SPEED_PER_SECOND = 0.125;

export function clampPanoramaPosition(position: number): number {
  return Math.min(1, Math.max(0, position));
}

export function panoramaPositionAtElapsed({
  startPosition,
  direction,
  elapsedMs,
}: {
  startPosition: number;
  direction: PanoramaDirection;
  elapsedMs: number;
}): number {
  const movingDurationSeconds =
    Math.max(0, elapsedMs - PANORAMA_HOLD_DELAY_MS) / 1_000;
  const directionSign = direction === "left" ? -1 : 1;

  return clampPanoramaPosition(
    startPosition +
      directionSign * PANORAMA_SPEED_PER_SECOND * movingDurationSeconds,
  );
}
