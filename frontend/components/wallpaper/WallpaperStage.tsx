"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getMemoryObjects, type MemoryObjectItem } from "@/lib/api";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import {
  getSelectedWallpaper,
  useSceneStore,
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

export function WallpaperStage() {
  useWallpaperSync();
  const { t } = useI18n();

  const userContext = useOnboardingStore((s) => s.userContext);
  const uiMode = useSceneStore((s) => s.uiMode);
  const focusMode = useSceneStore((s) => s.focusMode);
  const selectedWallpaper = useSceneStore(getSelectedWallpaper);
  const shiftWallpaper = useSceneStore((s) => s.shiftWallpaper);
  const [showAssets, setShowAssets] = useState(false);
  const [assets, setAssets] = useState<MemoryObjectItem[]>([]);
  const [isLoadingAssets, setIsLoadingAssets] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [isSubjectInteractionActive, setIsSubjectInteractionActive] =
    useState(false);
  const [cameraPositionX, setCameraPositionX] = useState(0.5);
  const swipeStartYRef = useRef<number | null>(null);
  const swipeStartXRef = useRef<number | null>(null);

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
  }, [userContext]);

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

  const foregroundHidden = focusMode !== "balanced";

  const handlePointerDownCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    if (event.pointerType === "mouse") return;
    const target = event.target;
    if (
      target instanceof Element &&
      target.closest("button, input, textarea, select, [role='button']")
    ) {
      swipeStartYRef.current = null;
      swipeStartXRef.current = null;
      return;
    }
    swipeStartYRef.current = event.clientY;
    swipeStartXRef.current = event.clientX;
  };

  const handlePointerUpCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    const startY = swipeStartYRef.current;
    const startX = swipeStartXRef.current;
    swipeStartYRef.current = null;
    swipeStartXRef.current = null;
    if (startY === null || startX === null) return;

    const deltaY = event.clientY - startY;
    const deltaX = event.clientX - startX;
    const isVerticalGesture = Math.abs(deltaY) > Math.abs(deltaX) * 1.25;
    if (isVerticalGesture) {
      if (Math.abs(deltaY) < SWIPE_THRESHOLD) return;
      if (!showAssets && deltaY < 0 && uiMode === "wallpaper") {
        setShowAssets(true);
      } else if (showAssets && deltaY > 0) {
        setShowAssets(false);
      }
      return;
    }

    const isHorizontalGesture = Math.abs(deltaX) > Math.abs(deltaY) * 1.25;
    if (
      isHorizontalGesture &&
      Math.abs(deltaX) >= SWIPE_THRESHOLD &&
      !showAssets &&
      uiMode === "wallpaper"
    ) {
      shiftWallpaper(deltaX < 0 ? 1 : -1);
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
