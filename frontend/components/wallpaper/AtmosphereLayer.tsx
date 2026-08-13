"use client";

import { useEffect, useRef, useState } from "react";
import {
  getSelectedWallpaper,
  isLatestWallpaper,
  useSceneStore,
} from "@/lib/hooks/useSceneStore";
import type { FocusMode } from "@/lib/hooks/useSceneStore";
import { envelopeToFilter } from "@/lib/wallpaperEnv";
import {
  normalizeWallpaperImageUrl,
  resolveApiAssetUrl,
  STATIC_BASE_SCENE_PATH,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { clampPanoramaPosition } from "@/lib/panoramaCamera";
import { WallpaperClockOverlay } from "./WallpaperClockOverlay";

const FOCUS_TRANSFORM: Record<FocusMode, string> = {
  balanced: "scale(1)",
  elder: "scale(1.45) translate(15%, -15%)",
  child: "scale(1.45) translate(-15%, 15%)",
};

const IMG_W = 1800;
const IMG_H = 3229;
const WALLPAPER_CROSSFADE_MS = 2000;
const PAGE_TRANSITION_MS = 220;

type AtmosphereLayerProps = {
  motionPaused?: boolean;
  cameraPositionX?: number;
  pageTransition?: "idle" | "exiting" | "entering";
  transitionDirection?: "next" | "prev";
};

function FocusToggleIcon({ isFocused }: { isFocused: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 64 64"
      fill="none"
      className="h-[52px] w-[52px]"
    >
      {isFocused ? (
        // 放大后：四个箭头朝内
        <path
          d="
            M8 8 24 24
            M24 24H13
            M24 24V13

            M56 8 40 24
            M40 24H51
            M40 24V13

            M8 56 24 40
            M24 40H13
            M24 40V51

            M56 56 40 40
            M40 40H51
            M40 40V51
          "
          stroke="currentColor"
          strokeWidth="6"
          strokeLinecap="square"
          strokeLinejoin="miter"
        />
      ) : (
        // 默认状态：四个箭头朝外
        <path
          d="
            M24 24 8 8
            M8 8H19
            M8 8V19

            M40 24 56 8
            M56 8H45
            M56 8V19

            M24 40 8 56
            M8 56H19
            M8 56V45

            M40 40 56 56
            M56 56H45
            M56 56V45
          "
          stroke="currentColor"
          strokeWidth="6"
          strokeLinecap="square"
          strokeLinejoin="miter"
        />
      )}
    </svg>
  );
}

export function FocusButton({
  isFocused,
  label,
  className,
  onClick,
}: {
  isFocused: boolean;
  label: string;
  className: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      aria-label={label}
      aria-pressed={isFocused}
      className={`focus-arrow absolute z-[60] flex h-14 w-14 items-center justify-center rounded-full bg-transparent text-white ${className}`}
    >
      <FocusToggleIcon isFocused={isFocused} />
    </button>
  );
}

export function AtmosphereLayer({
  motionPaused = false,
  cameraPositionX = 0.5,
  pageTransition = "idle",
  transitionDirection = "next",
}: AtmosphereLayerProps) {
  const { t } = useI18n();
  const selectedWallpaper = useSceneStore(getSelectedWallpaper);
  const isLatest = useSceneStore(isLatestWallpaper);
  const generatedWallpaperUrl = useSceneStore(
    (state) => state.generatedWallpaperUrl,
  );
  const uiMode = useSceneStore((state) => state.uiMode);
  const currentDayIndex = useSceneStore((state) => state.currentDayIndex);
  const envelope = useSceneStore((state) => state.wallpaperEnvelope);
  const focusMode = useSceneStore((state) => state.focusMode);
  const setFocusMode = useSceneStore((state) => state.setFocusMode);

  // Prefer generatedWallpaperUrl when on today's latest wallpaper.
  // When user has navigated to a past day or an older revision,
  // show that specific wallpaper's imageUrl instead.
  // Fallback to static base scene (never legacy demo paths).
  const sourceUrl = normalizeWallpaperImageUrl(
    isLatest && generatedWallpaperUrl
      ? generatedWallpaperUrl
      : selectedWallpaper?.imageUrl ||
          resolveApiAssetUrl(STATIC_BASE_SCENE_PATH),
  );

  const resolvedSourceUrl = sourceUrl.startsWith("/generated/")
    ? resolveApiAssetUrl(sourceUrl)
    : sourceUrl;
  const displayedUrlRef = useRef(resolvedSourceUrl);
  const requestedUrlRef = useRef(resolvedSourceUrl);
  const pendingUrlRef = useRef<string | null>(null);
  const transitionInProgressRef = useRef(false);
  const mountedRef = useRef(true);
  const firstFrameRef = useRef<number>();
  const secondFrameRef = useRef<number>();
  const settleTimerRef = useRef<number>();
  const [displayedUrl, setDisplayedUrl] = useState(resolvedSourceUrl);
  const [previousUrl, setPreviousUrl] = useState<string | null>(null);
  const [crossfadeActive, setCrossfadeActive] = useState(false);

  const startCrossfade = (nextUrl: string) => {
    if (!mountedRef.current || nextUrl === displayedUrlRef.current) return;
    if (transitionInProgressRef.current) {
      pendingUrlRef.current = nextUrl;
      return;
    }

    transitionInProgressRef.current = true;
    const outgoingUrl = displayedUrlRef.current;
    displayedUrlRef.current = nextUrl;
    setPreviousUrl(outgoingUrl);
    setDisplayedUrl(nextUrl);
    setCrossfadeActive(false);

    firstFrameRef.current = window.requestAnimationFrame(() => {
      secondFrameRef.current = window.requestAnimationFrame(() => {
        if (!mountedRef.current) return;
        setCrossfadeActive(true);
        settleTimerRef.current = window.setTimeout(() => {
          if (!mountedRef.current) return;
          setPreviousUrl(null);
          setCrossfadeActive(false);
          transitionInProgressRef.current = false;
          const pendingUrl = pendingUrlRef.current;
          pendingUrlRef.current = null;
          if (pendingUrl && pendingUrl !== displayedUrlRef.current) {
            preloadAndTransition(pendingUrl);
          }
        }, WALLPAPER_CROSSFADE_MS);
      });
    });
  };

  const preloadAndTransition = (nextUrl: string) => {
    if (!mountedRef.current || nextUrl === displayedUrlRef.current) return;
    const preload = new window.Image();
    const begin = () => {
      if (!mountedRef.current || nextUrl !== requestedUrlRef.current) return;
      startCrossfade(nextUrl);
    };
    preload.decoding = "async";
    preload.onload = begin;
    preload.src = nextUrl;
    if (preload.complete && preload.naturalWidth > 0) begin();
  };

  useEffect(() => {
    requestedUrlRef.current = resolvedSourceUrl;
    preloadAndTransition(resolvedSourceUrl);
  }, [resolvedSourceUrl]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (firstFrameRef.current !== undefined) {
        window.cancelAnimationFrame(firstFrameRef.current);
      }
      if (secondFrameRef.current !== undefined) {
        window.cancelAnimationFrame(secondFrameRef.current);
      }
      if (settleTimerRef.current !== undefined) {
        window.clearTimeout(settleTimerRef.current);
      }
    };
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

  if (sourceUrl) {
    return (
      <>
        <div
          className="focus-scene-wrapper absolute inset-0"
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
            style={
              pageTransition !== "idle"
                ? {
                    animation:
                      pageTransition === "exiting"
                ? `wallpaper-slide-out ${PAGE_TRANSITION_MS}ms ease-in-out forwards`
                : `wallpaper-slide-in ${PAGE_TRANSITION_MS}ms ease-out forwards`,
                  }
                : {
                    transform: `translate3d(0, 0, 0) scale(${cameraScale})`,
                  }
            }
          >
            <style>{`
              @keyframes wallpaper-slide-out {
                0%   { opacity: 1; transform: translateX(0) scale(${cameraScale}); }
                100% { opacity: 0.55; transform: translateX(${transitionDirection === "next" ? "-6%" : "6%"}) scale(${cameraScale}); }
              }
              @keyframes wallpaper-slide-in {
                0%   { opacity: 0.55; transform: translateX(${transitionDirection === "next" ? "6%" : "-6%"}) scale(${cameraScale}); }
                100% { opacity: 1; transform: translateX(0) scale(${cameraScale}); }
              }
            `}</style>
            {previousUrl ? (
              <div
                aria-hidden="true"
                className="wallpaper-crossfade-layer focus-scene absolute inset-0 bg-cover bg-center bg-no-repeat"
                style={{
                  backgroundImage: `url(${previousUrl})`,
                  backgroundPosition: `${cameraPositionPercent}% center`,
                  filter: envelopeToFilter(envelope),
                  opacity: crossfadeActive ? 0 : 1,
                  transition: `opacity ${WALLPAPER_CROSSFADE_MS}ms ease-in-out, filter 2400ms ease-out, transform 800ms ease-in-out, background-position 120ms linear`,
                  transform: focusTransform,
                }}
              />
            ) : null}
            <div
              data-wallpaper-image
              className="wallpaper-crossfade-layer focus-scene absolute inset-0 bg-cover bg-center bg-no-repeat"
              style={{
                backgroundImage: `url(${displayedUrl})`,
                backgroundPosition: `${cameraPositionPercent}% center`,
                filter: envelopeToFilter(envelope),
                opacity: previousUrl ? (crossfadeActive ? 1 : 0) : 1,
                transition: `opacity ${WALLPAPER_CROSSFADE_MS}ms ease-in-out, filter 2400ms ease-out, transform 800ms ease-in-out, background-position 120ms linear`,
                transform: focusTransform,
              }}
            />
          </div>
        </div>

        {/* Clock overlay — outside the focus transform wrapper so it never
            scales or translates when the user switches elder/child focus. */}
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

          .wallpaper-crossfade-layer {
            will-change: opacity;
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

          .focus-arrow {
            transition:
              opacity 200ms ease-out,
              filter 200ms ease-out;
          }

          .focus-arrow:active {
            opacity: 0.7;
          }

          .focus-arrow svg {
            filter: drop-shadow(0 2px 6px rgba(0, 0, 0, 0.42));
          }

          @media (prefers-reduced-motion: reduce) {
            .pre-wallpaper {
              animation: none;
              opacity: 1;
            }

            .wallpaper-motion-surface {
              transform: none !important;
            }

            .wallpaper-crossfade-layer {
              transition: none !important;
            }
          }
        `}</style>

        {uiMode === "wallpaper" ? (
          <>
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
          </>
        ) : null}
      </>
    );
  }

  return (
    <div className="absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 50% 40%, rgba(255,244,214,1) 0%, rgba(255,244,214,0) 40%), " +
            "linear-gradient(to bottom, #fcd9b6 0%, #f5b78a 45%, #825b46 100%)",
          backgroundRepeat: "no-repeat",
          backgroundSize: "100% 100%",
          backgroundPosition: "center center",
        }}
      />

      <div
        className="absolute"
        style={{
          left: `${(1200 / IMG_W) * 100}%`,
          top: `${(720 / IMG_H) * 100}%`,
          width: 0,
          height: 0,
          transform: "translate(-50%, -50%)",
        }}
      >
        <div
          style={{
            width: `${(240 / IMG_W) * 100}vw`,
            aspectRatio: `${240} / ${600}`,
            background: "#704a37",
            borderRadius: 40,
            position: "relative",
          }}
        >
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: `${(120 / (600 + 240)) * -100}%`,
              transform: "translateX(-50%)",
              width: `${(240 / 240) * 100}%`,
              paddingTop: "100%",
              background: "#704a37",
              borderRadius: "50%",
            }}
          />
        </div>
      </div>

      <div
        className="absolute"
        style={{
          left: `${(280 / IMG_W) * 100}%`,
          top: `${(2050 / IMG_H) * 100}%`,
          width: 0,
          height: 0,
          transform: "translate(-50%, -50%)",
        }}
      >
        <div
          style={{
            width: `${(200 / IMG_W) * 100}vw`,
            aspectRatio: `${200} / ${500}`,
            background: "#f7e9c4",
            borderRadius: 30,
            position: "relative",
          }}
        >
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: `${(100 / (500 + 200)) * -100}%`,
              transform: "translateX(-50%)",
              width: `${(200 / 200) * 100}%`,
              paddingTop: "100%",
              background: "#f7e9c4",
              borderRadius: "50%",
            }}
          />
        </div>
      </div>

      <div
        className="absolute inset-x-0 bottom-0"
        style={{
          height: `${((3229 - 2500) / IMG_H) * 100}%`,
          background:
            "linear-gradient(to bottom, rgba(161,115,90,0) 0%, rgba(161,115,90,0.5) 60%, rgba(161,115,90,0.5) 100%)",
        }}
      />
    </div>
  );
}
