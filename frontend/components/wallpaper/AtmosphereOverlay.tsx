"use client";

/**
 * AtmosphereOverlay
 *
 * Carousel-aware sibling of AtmosphereLayer. It owns ONLY:
 *   - the focus-mode transform (balanced / elder / child)
 *   - the pre-wallpaper stabilization + breathe animation
 *   - the WallpaperClockOverlay
 *   - the focus arrow buttons (elder / child)
 *
 * It does NOT render the wallpaper image. The image is rendered by
 * WallpaperCarousel as a stable flex row of pages so that history paging
 * uses a true translate3d drag instead of the legacy 2s crossfade.
 *
 * AtmosphereLayer (kept dormant alongside the rest of the previous build)
 * continues to expose the legacy crossfade; this overlay is what the
 * current build actually mounts.
 */

import { useEffect, useState } from "react";
import {
  useSceneStore,
  type FocusMode,
} from "@/lib/hooks/useSceneStore";
import { clampPanoramaPosition } from "@/lib/panoramaCamera";
import { useI18n } from "@/lib/i18n";
import { WallpaperClockOverlay } from "./WallpaperClockOverlay";
import { FocusButton } from "./AtmosphereLayer";

const FOCUS_TRANSFORM: Record<FocusMode, string> = {
  balanced: "scale(1)",
  elder: "scale(1.45) translate(15%, -15%)",
  child: "scale(1.45) translate(-15%, 15%)",
};

type Props = {
  motionPaused?: boolean;
  cameraPositionX?: number;
};

export function AtmosphereOverlay({
  motionPaused = false,
  cameraPositionX = 0.5,
}: Props) {
  const { t } = useI18n();
  const currentDayIndex = useSceneStore((state) => state.currentDayIndex);
  const focusMode = useSceneStore((state) => state.focusMode);
  const setFocusMode = useSceneStore((state) => state.setFocusMode);
  const uiMode = useSceneStore((state) => state.uiMode);

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const focusTransform = FOCUS_TRANSFORM[focusMode];
  const sceneDim = focusMode === "balanced" ? 1 : 0.85;
  const childIsFocused = focusMode === "child";
  const elderIsFocused = focusMode === "elder";
  const effectiveCameraPosition =
    focusMode === "balanced" ? clampPanoramaPosition(cameraPositionX) : 0.5;
  const cameraPositionPercent = effectiveCameraPosition * 100;
  const cameraScale =
    focusMode === "balanced"
      ? 1.015 + Math.abs(effectiveCameraPosition - 0.5) * 0.03
      : 1.015;

  return (
    <>
      {/* Focus / breathe wrapper that overlays the WallpaperCarousel.
          pointer-events: none so it never intercepts the carousel drag. */}
      <div
        className="focus-scene-wrapper pointer-events-none absolute inset-0"
        style={{
          opacity: sceneDim,
          transition: "opacity 800ms ease-in-out",
        }}
      >
        <div
          data-wallpaper-motion-surface
          data-motion-paused={motionPaused ? "true" : "false"}
          data-wallpaper-position-x={`${cameraPositionPercent}%`}
          className="wallpaper-motion-surface pre-wallpaper absolute inset-0"
          style={{
            transform: `translate3d(0, 0, 0) scale(${cameraScale})`,
          }}
        />
      </div>

      {/* Clock overlay — outside the focus wrapper so it never scales. */}
      <WallpaperClockOverlay dayIndex={currentDayIndex} />

      <style jsx>{`
        .pre-wallpaper {
          opacity: 0;
          animation:
            pre-wallpaper-stabilize 3000ms ease-out forwards,
            pre-wallpaper-breathe 12000ms ease-in-out 3000ms infinite;
        }

        .wallpaper-motion-surface {
          transform-origin: 50% 50%;
          will-change: transform;
          transition: transform 160ms linear;
        }

        @keyframes pre-wallpaper-stabilize {
          from {
            opacity: 0;
          }
          to {
            opacity: 1;
          }
        }

        @keyframes pre-wallpaper-breathe {
          0%,
          100% {
            opacity: 1;
          }
          50% {
            opacity: 0.98;
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .pre-wallpaper {
            animation: none;
            opacity: 1;
          }

          .wallpaper-motion-surface {
            transform: none !important;
          }
        }
      `}</style>

      {uiMode === "wallpaper" ? (
        <FocusButton
          isFocused={childIsFocused}
          label={
            childIsFocused
              ? t("atmosphere.returnFull")
              : t("atmosphere.enlargeUpper")
          }
          className="right-2 top-2"
          onClick={() =>
            setFocusMode(childIsFocused ? "balanced" : "child")
          }
        />
      ) : null}

      {uiMode === "wallpaper" ? (
        <FocusButton
          isFocused={elderIsFocused}
          label={
            elderIsFocused
              ? t("atmosphere.returnFull")
              : t("atmosphere.enlargeLower")
          }
          className="bottom-2 left-2"
          onClick={() =>
            setFocusMode(elderIsFocused ? "balanced" : "elder")
          }
        />
      ) : null}
    </>
  );
}