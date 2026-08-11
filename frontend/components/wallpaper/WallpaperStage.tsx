"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getMemoryObjects, type MemoryObjectItem } from "@/lib/api";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import {
  getSelectedWallpaper,
  isLatestWallpaper,
  isFirstWallpaper,
  useSceneStore,
  DAY_LABELS,
} from "@/lib/hooks/useSceneStore";
import { useWallpaperSync } from "@/lib/hooks/useWallpaperSync";
import { AtmosphereLayer } from "./AtmosphereLayer";
import { ChatOverlay } from "./ChatOverlay";
import { MemoryAssetsPanel } from "./MemoryAssetsPanel";
import { PanoramaHoldControls } from "./PanoramaHoldControls";
import { PetalMotionLayer } from "./PetalMotionLayer";
import { SubjectLiftLayer } from "./SubjectLiftLayer";
import { useI18n } from "@/lib/i18n";
import { subscribeToWallpaperEvents } from "@/lib/wallpaperEvents";

const SWIPE_THRESHOLD = 70;
const PAGE_TRANSITION_MS = 220;

export function WallpaperStage() {
  useWallpaperSync();
  const { t } = useI18n();

  const userContext = useOnboardingStore((s) => s.userContext);
  const uiMode = useSceneStore((s) => s.uiMode);
  const focusMode = useSceneStore((s) => s.focusMode);
  const selectedWallpaper = useSceneStore(getSelectedWallpaper);
  const isLatest = useSceneStore(isLatestWallpaper);
  const isFirst = useSceneStore(isFirstWallpaper);
  const shiftWallpaper = useSceneStore((s) => s.shiftWallpaper);
  const wallpapersByDay = useSceneStore((s) => s.wallpapersByDay);
  const toggleUiMode = useSceneStore((s) => s.toggleUiMode);
  const [showAssets, setShowAssets] = useState(false);
  const [assets, setAssets] = useState<MemoryObjectItem[]>([]);
  const [isLoadingAssets, setIsLoadingAssets] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [isSubjectInteractionActive, setIsSubjectInteractionActive] =
    useState(false);
  const [cameraPositionX, setCameraPositionX] = useState(0.5);

  // ── Swipe gesture state ────────────────────────────────────────────
  const swipeStartYRef = useRef<number | null>(null);
  const swipeStartXRef = useRef<number | null>(null);
  // Guards against a single pointer sequence being consumed by multiple actions.
  const gestureConsumedRef = useRef(false);

  // ── Page transition animation ──────────────────────────────────────
  const [pageTransition, setPageTransition] = useState<
    "idle" | "exiting" | "entering"
  >("idle");
  const transitionDirectionRef = useRef<"next" | "prev">("next");
  const transitionTimerRef = useRef<number | null>(null);

  // ── Computed: flat list of wallpapers with real history ─────────────
  const availableWallpapers = (() => {
    const result: Array<{ dayIndex: number; wallpaperIndex: number }> = [];
    for (let d = DAY_LABELS.length - 1; d >= 0; d--) {
      const dayItems = wallpapersByDay[DAY_LABELS[d]];
      if (!dayItems || dayItems.length === 0) continue;
      for (let w = dayItems.length - 1; w >= 0; w--) {
        result.push({ dayIndex: d, wallpaperIndex: w });
      }
    }
    return result;
  })();

  const currentDayIndex = useSceneStore((s) => s.currentDayIndex);
  const currentWallpaperIndex = useSceneStore((s) => s.currentWallpaperIndex);
  const currentPageIndex = availableWallpapers.findIndex(
    (p) =>
      p.dayIndex === currentDayIndex &&
      p.wallpaperIndex === currentWallpaperIndex,
  );

  const loadAssets = useCallback(async () => {
    if (!userContext) {
      setAssetError(t("wallpaperStage.errorRelationship"));
      return;
    }
    setIsLoadingAssets(true);
    setAssetError(null);
    try {
      const result = await getMemoryObjects({
        userId: userContext.userId,
        relationshipId: userContext.relationshipId,
        generateMissing: true,
      });
      setAssets(result.items);
    } catch (error) {
      console.error("[MemoryAssets] load failed", error);
      setAssetError(t("wallpaperStage.errorLoadMemory"));
    } finally {
      setIsLoadingAssets(false);
    }
  }, [userContext, t]);

  useEffect(() => {
    if (!showAssets) return;
    return subscribeToWallpaperEvents((event) => {
      if (event.status !== "memory_object_ready" || !event.memoryObject) return;
      const incoming = event.memoryObject;
      setAssets((current) => {
        const index = current.findIndex(
          (item) =>
            item.assetId === incoming.assetId || item.name === incoming.name,
        );
        if (index < 0) return [...current, incoming];
        const next = [...current];
        next[index] = incoming;
        return next;
      });
    });
  }, [showAssets]);

  useEffect(() => {
    if (showAssets) void loadAssets();
  }, [loadAssets, showAssets]);

  useEffect(() => {
    setCameraPositionX(0.5);
  }, [selectedWallpaper?.revisionId]);

  // Cancel any pending page transition when wallpaper changes.
  useEffect(() => {
    if (transitionTimerRef.current) {
      clearTimeout(transitionTimerRef.current);
      transitionTimerRef.current = null;
    }
    setPageTransition("idle");
  }, [selectedWallpaper?.revisionId]);

  const foregroundHidden = focusMode !== "balanced";

  // ── Gesture handler ─────────────────────────────────────────────────
  // Unified pointer gesture classifier that prevents Subject Lift, page
  // navigation, and panel open from firing simultaneously for the same
  // pointer sequence. Subject Lift has priority: if it is active (or the
  // user just released from it), no page/panel navigation fires.
  const handlePointerDownCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    if (event.pointerType === "mouse") return;
    const target = event.target;
    // Exclude interactive elements from gesture detection.
    if (
      target instanceof Element &&
      target.closest(
        "button, input, textarea, select, [role='button'], [data-panorama-hold-control]",
      )
    ) {
      swipeStartYRef.current = null;
      swipeStartXRef.current = null;
      return;
    }
    swipeStartYRef.current = event.clientY;
    swipeStartXRef.current = event.clientX;
    gestureConsumedRef.current = false;
  };

  const handlePointerUpCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    const startY = swipeStartYRef.current;
    const startX = swipeStartXRef.current;
    swipeStartYRef.current = null;
    swipeStartXRef.current = null;
    if (startY === null || startX === null) return;
    if (gestureConsumedRef.current) return;

    const deltaY = event.clientY - startY;
    const deltaX = event.clientX - startX;

    // ── Vertical gesture ───────────────────────────────────────────
    const isVerticalGesture =
      Math.abs(deltaY) > Math.abs(deltaX) * 1.25;
    if (isVerticalGesture) {
      if (Math.abs(deltaY) < SWIPE_THRESHOLD) return;
      if (gestureConsumedRef.current) return;

      // Subject Lift active → suppress all navigation gestures.
      if (isSubjectInteractionActive) return;

      if (deltaY < 0 && !showAssets && uiMode === "wallpaper") {
        // Swipe up → open Memory Assets.
        gestureConsumedRef.current = true;
        setShowAssets(true);
        return;
      }
      if (deltaY > 0 && !showAssets && uiMode === "wallpaper") {
        // Swipe down → open AI Summary.
        gestureConsumedRef.current = true;
        toggleUiMode();
        return;
      }
      return;
    }

    // ── Horizontal gesture ────────────────────────────────────────
    const isHorizontalGesture =
      Math.abs(deltaX) > Math.abs(deltaY) * 1.25;
    if (
      isHorizontalGesture &&
      Math.abs(deltaX) >= SWIPE_THRESHOLD &&
      !showAssets &&
      uiMode === "wallpaper"
    ) {
      // Subject Lift active → suppress page navigation.
      if (isSubjectInteractionActive) return;
      if (gestureConsumedRef.current) return;

      gestureConsumedRef.current = true;
      const dir = deltaX < 0 ? 1 : -1;
      transitionDirectionRef.current = dir < 0 ? "next" : "prev";
      setPageTransition("exiting");
      if (transitionTimerRef.current !== null) {
        clearTimeout(transitionTimerRef.current);
      }
      transitionTimerRef.current = window.setTimeout(() => {
        shiftWallpaper(dir);
        setPageTransition("entering");
        transitionTimerRef.current = window.setTimeout(() => {
          setPageTransition("idle");
          transitionTimerRef.current = null;
        }, PAGE_TRANSITION_MS);
      }, PAGE_TRANSITION_MS);
    }
  };

  return (
    <div
      className="wallpaper-root"
      onPointerDownCapture={handlePointerDownCapture}
      onPointerUpCapture={handlePointerUpCapture}
    >
      <AtmosphereLayer
        motionPaused={isSubjectInteractionActive}
        cameraPositionX={cameraPositionX}
        pageTransition={pageTransition}
        transitionDirection={transitionDirectionRef.current}
      />
      <PetalMotionLayer
        hidden={foregroundHidden}
        motionPaused={foregroundHidden || isSubjectInteractionActive}
      />
      <SubjectLiftLayer
        onInteractionActiveChange={setIsSubjectInteractionActive}
      />
      <ChatOverlay />

      {!showAssets &&
      uiMode === "wallpaper" &&
      selectedWallpaper &&
      focusMode === "balanced" ? (
        <PanoramaHoldControls
          position={cameraPositionX}
          disabled={isSubjectInteractionActive}
          onPositionChange={setCameraPositionX}
          onShortPress={(direction) =>
            shiftWallpaper(direction === "left" ? -1 : 1)
          }
        />
      ) : null}

      {/* Bottom pagination dots — visible when there are 2+ real wallpapers */}
      {!showAssets &&
      uiMode === "wallpaper" &&
      availableWallpapers.length >= 2 ? (
        <div
          className="pointer-events-none absolute bottom-8 left-0 right-0 z-40 flex justify-center gap-2"
          aria-hidden
        >
          {availableWallpapers.map((_, i) => {
            const isActive = i === currentPageIndex;
            return (
              <div
                key={i}
                className={`rounded-full transition-all duration-200 ${
                  isActive
                    ? "h-2 w-5 bg-white/90 shadow-sm"
                    : "h-2 w-2 bg-white/40"
                }`}
              />
            );
          })}
        </div>
      ) : null}

      {!showAssets && uiMode === "wallpaper" ? (
        <button
          type="button"
          aria-label={t("wallpaperStage.openRecentMemory")}
          onClick={() => setShowAssets(true)}
          className="absolute left-20 right-20 top-0 z-[70] h-10 bg-transparent"
        />
      ) : null}

      {showAssets && uiMode === "wallpaper" ? (
        <MemoryAssetsPanel
          assets={assets}
          isLoading={isLoadingAssets}
          error={assetError}
          onClose={() => setShowAssets(false)}
          onRetry={() => void loadAssets()}
          onAssetSelect={(asset) => {
            console.log("[MemoryAssets] selected", asset.assetId, asset.name);
          }}
        />
      ) : null}
    </div>
  );
}
