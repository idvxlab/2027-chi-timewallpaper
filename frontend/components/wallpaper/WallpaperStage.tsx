"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMemoryObjects, type MemoryObjectItem, STATIC_BASE_SCENE_PATH, resolveApiAssetUrl } from "@/lib/api";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import {
  getSelectedWallpaper,
  useSceneStore,
  DAY_LABELS,
  TODAY_INDEX,
} from "@/lib/hooks/useSceneStore";
import { useWallpaperSync } from "@/lib/hooks/useWallpaperSync";
import { subscribeGenerationSuccessGlobal } from "@/lib/hooks/useWallpaperVoiceEditRecorder";
import { useHoldAnywhereRecorder } from "@/lib/hooks/useHoldAnywhereRecorder";
import { AtmosphereOverlay } from "./AtmosphereOverlay";
import {
  WallpaperCarousel,
  type WallpaperCarouselHandle,
} from "./WallpaperCarousel";
import { ChatOverlay } from "./ChatOverlay";
import { MemoryAssetsPanel } from "./MemoryAssetsPanel";
import { PanoramaHoldControls } from "./PanoramaHoldControls";
import { PetalMotionLayer } from "./PetalMotionLayer";
import { RecordingHalo } from "./RecordingHalo";
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
  const wallpapersByDay = useSceneStore((s) => s.wallpapersByDay);
  const wallpaperStateHydrated = useSceneStore((s) => s.wallpaperStateHydrated);
  const toggleUiMode = useSceneStore((s) => s.toggleUiMode);

  // ── Derived gating flags ────────────────────────────────────────────────
  // True when a generated shared wallpaper exists and hold-anywhere is allowed.
  // Hold-anywhere is the primary voice interaction for wallpaper canvas.
  // It is active whenever wallpaper mode is on, a real wallpaper revision exists,
  // and hydration is complete. We intentionally do NOT gate on interactionMode
  // (which returns "history_disabled" for historical revisions) so that
  // hold-anywhere works on both latest AND historical revisions — the backend
  // always generates from the latest regardless of where the user is swiped.
  const hasAnyWallpaperRevision =
    wallpapersByDay &&
    Object.values(wallpapersByDay).some((items) => items && items.length > 0);
  const canUseHoldAnywhere =
    uiMode === "wallpaper" && wallpaperStateHydrated && hasAnyWallpaperRevision;
  const hasGeneratedWallpaper =
    uiMode === "wallpaper" && wallpaperStateHydrated && hasAnyWallpaperRevision;

  const [showAssets, setShowAssets] = useState(false);
  const [assets, setAssets] = useState<MemoryObjectItem[]>([]);
  const [isLoadingAssets, setIsLoadingAssets] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [isSubjectInteractionActive, setIsSubjectInteractionActive] =
    useState(false);
  const [cameraPositionX, setCameraPositionX] = useState(0.5);

  // ── Hold-Anywhere Voice Gesture ──────────────────────────────────
  // Replaces the previous Subject Lift pointer path. SubjectLiftLayer
  // is left in the codebase (dormant) for future use; this hook is the
  // authoritative voice gesture for the test build.
  const holdAnywhere = useHoldAnywhereRecorder({
    subjectLiftHeld: isSubjectInteractionActive,
    uiMode,
    enabled: canUseHoldAnywhere,
    onSwipeCancel: (pointerId, axis) => {
      // The user clearly swiped horizontally or vertically while holding;
      // hand the gesture to whichever handler owns that axis. We must clear
      // voice ownership SYNCHRONOUSLY here so the very next pointerMove in
      // the same event sequence is treated as a carousel drag (or so the
      // pointerUp handler can run the vertical gesture logic).
      voiceGestureActiveRef.current = false;
      voiceGesturePointerIdRef.current = null;
      swipeStartXRef.current = swipeStartXRefOnDownRef.current;
      swipeStartYRef.current = swipeStartYRefOnDownRef.current;
      gestureConsumedRef.current = false;
      console.log(
        "[WallpaperStage] hold→" + axis + " handoff (pointerId=" + pointerId + ")",
      );
    },
  });
// Standalone module-level subscription surface — no recorder instance.
  // We destructure to keep the existing local name `subscribeGenerationSuccess`
  // so the auto-jump useEffect below needs no changes.
  const subscribeGenerationSuccess = subscribeGenerationSuccessGlobal;
  // (Emit side lives in the hook instance created by useHoldAnywhereRecorder.
  //  Module-level listener storage ensures cross-instance visibility.)
  // ── Auto-jump to latest after historical generation success ─────────────
  // When the user records on a historical revision, the backend generates from
  // the latest. When the new revision arrives via SSE, we must auto-jump to
  // latest (TODAY_INDEX, newest revision) so the user sees the result.
  // We subscribe to generation success and track the expected eventSeq.
  const setCurrentDayIndex = useSceneStore((s) => s.setCurrentDayIndex);
  const setSelectedRevision = useSceneStore((s) => s.setSelectedRevision);
  const expectedJumpEventSeqRef = useRef<number>(0);

  // useEffect with no deps: subscribes once on mount, unsubscribes on unmount.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const unsubscribe = subscribeGenerationSuccess(
      (payload: {
        eventSeq: number;
        captureId: string;
        relationshipId: string;
        isFromHistoricalPage: boolean;
      }) => {
        const { eventSeq } = payload;
        if (eventSeq <= 0) return;
        console.log("[WallpaperStage] generation success, expecting jump to eventSeq", eventSeq);
        expectedJumpEventSeqRef.current = eventSeq;
      },
    );
    return unsubscribe;
  // NOTE: subscribeGenerationSuccess is stable; no need to re-subscribe when it changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React to wallpapersByDay changes: check if the expected revision has arrived.
  // NOTE: This effect intentionally has no dependency on holdAnywhere or other
  // local state — it only cares about store changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const expectedSeq = expectedJumpEventSeqRef.current;
    if (!expectedSeq) return;

    const todayWallpapers = wallpapersByDay[DAY_LABELS[TODAY_INDEX]] ?? [];
    const arrived = todayWallpapers.find((w) => w.eventSeq === expectedSeq);
    if (arrived) {
      const newIndex = todayWallpapers.indexOf(arrived);
      console.log("[WallpaperStage] expected revision arrived, auto-jumping to latest", {
        eventSeq: expectedSeq,
        newIndex,
      });
      expectedJumpEventSeqRef.current = 0;
      // Jump to TODAY_INDEX + the newest revision (last in the array = latest).
      setCurrentDayIndex(TODAY_INDEX);
      setSelectedRevision(TODAY_INDEX, todayWallpapers.length - 1);
    }
  // Watch wallpapersByDay so this fires when the new revision appears.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wallpapersByDay]);

  // Capture pointerDown coords here so the swipe-cancel callback can
  // hand them to the carousel even if hold-anywhere already reset its
  // own copies.
  const swipeStartXRefOnDownRef = useRef<number | null>(null);
  const swipeStartYRefOnDownRef = useRef<number | null>(null);
  const [haloVisible, setHaloVisible] = useState(false);
  const [haloPhase, setHaloPhase] = useState<"starting" | "recording" | "exiting">(
    "starting",
  );
  const exitTimerRef = useRef<number | null>(null);

  // ── Carousel handle (page data lives after availableWallpapers below) ──
  const carouselRef = useRef<WallpaperCarouselHandle>(null);

  // ── Computed: flat list of wallpapers with real history ─────────────
  // NEWEST first: today → yesterday → ... → 6天前. The carousel renders
  // them in this order so swiping LEFT (deltaX < 0) goes to OLDER
  // revisions, mirroring Apple's Photos / iOS Pages apps.
  const availableWallpapers = useMemo(() => {
    const result: Array<{ dayIndex: number; wallpaperIndex: number }> = [];
    for (let d = DAY_LABELS.length - 1; d >= 0; d--) {
      const dayItems = wallpapersByDay[DAY_LABELS[d]];
      if (!dayItems || dayItems.length === 0) continue;
      for (let w = dayItems.length - 1; w >= 0; w--) {
        result.push({ dayIndex: d, wallpaperIndex: w });
      }
    }
    return result;
  }, [wallpapersByDay]);

  const currentDayIndex = useSceneStore((s) => s.currentDayIndex);
  const currentWallpaperIndex = useSceneStore((s) => s.currentWallpaperIndex);
  const currentPageIndex = availableWallpapers.findIndex(
    (p) =>
      p.dayIndex === currentDayIndex &&
      p.wallpaperIndex === currentWallpaperIndex,
  );

  const generatedWallpaperUrl = useSceneStore((s) => s.generatedWallpaperUrl);

  const carouselPages = useMemo(() => {
    return availableWallpapers.map((entry, visualIndex) => {
      const day = wallpapersByDay[DAY_LABELS[entry.dayIndex]] ?? [];
      const item = day[entry.wallpaperIndex];
      // First page (visualIndex === 0) is the LATEST revision.
      // For the latest revision, prefer the live generated URL so the
      // most recent generation is visible without waiting for any sync.
      const isLatestRevision =
        entry.dayIndex === TODAY_INDEX && entry.wallpaperIndex === 0;
      const imageUrl =
        isLatestRevision && generatedWallpaperUrl
          ? generatedWallpaperUrl
          : item?.imageUrl || "";
      return {
        key: item?.revisionId || `${entry.dayIndex}:${entry.wallpaperIndex}`,
        imageUrl,
        visualIndex,
        // Per-page metadata so onPageSettled can commit the right revision.
        dayIndex: entry.dayIndex,
        wallpaperIndex: entry.wallpaperIndex,
      };
    });
  }, [availableWallpapers, wallpapersByDay, generatedWallpaperUrl]);

  // Clamp currentPageIndex for the carousel. If the store points at a
  // revision that disappeared (e.g. transient empty bucket), default to 0.
  const safeCarouselIndex =
    currentPageIndex >= 0 && currentPageIndex < carouselPages.length
      ? currentPageIndex
      : 0;

  // ── Swipe gesture state ────────────────────────────────────────────
  const swipeStartYRef = useRef<number | null>(null);
  const swipeStartXRef = useRef<number | null>(null);
  // Guards against a single pointer sequence being consumed by multiple actions.
  const gestureConsumedRef = useRef(false);

  // ── Voice gesture ownership (synchronous, event-handler driven) ──
  // TRUE means the current pointer sequence is owned by the voice gesture
  // (hold-anywhere). The carousel / vertical gesture code must yield.
  // Set synchronously in handlePointerDownCapture when holdAnywhere claims
  // the gesture; reset in pointerUp / pointerCancel / onSwipeCancel.
  // MUST NOT be mutated inside a useEffect — see handler comments.
  const voiceGestureActiveRef = useRef(false);

  // Mirror that captures the voice gesture's *exact* pointerDown coordinates
  // (not whatever the carousel's swipeStartXRef currently holds, since the
  // carousel swipeStart was cleared while hold-anywhere was the owner).
  const voiceGesturePointerIdRef = useRef<number | null>(null);

  // ── Page transition animation (removed: now handled by WallpaperCarousel) ──
  // No pageTransition state here. The carousel commits store index from
  // its own transitionend, with the visual track already aligned.

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

  // ── Halo visibility tied to hold-anywhere state machine ────────────
  useEffect(() => {
    if (exitTimerRef.current !== null) {
      window.clearTimeout(exitTimerRef.current);
      exitTimerRef.current = null;
    }
    const status = holdAnywhere.state.status;
    // Halo visibility is bound to the actual recording lifecycle, not to
    // hold_pending. During the 2s candidate window the user has NOT
    // committed to recording — no halo, no audio. Halo only appears once
    // voice has OWNED the pointer (starting after 2s timer, or recording
    // after start() resolves).
    if (status === "starting" || status === "recording") {
      setHaloVisible(true);
      setHaloPhase(status === "recording" ? "recording" : "starting");
      setIsSubjectInteractionActive(true);
    } else if (status === "hold_pending") {
      // hold_pending = candidate window. Keep halo hidden; idle drag has
      // no commitment yet.
      setIsSubjectInteractionActive(false);
    } else if (status === "idle") {
      if (haloVisible) {
        setHaloPhase("exiting");
        exitTimerRef.current = window.setTimeout(() => {
          setHaloVisible(false);
          exitTimerRef.current = null;
        }, 200);
      }
      setIsSubjectInteractionActive(false);
    }
    return () => {
      if (exitTimerRef.current !== null) {
        window.clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
  }, [holdAnywhere.state.status, haloVisible]);
  // NOTE: voiceGestureActiveRef.current is NEVER mutated from a useEffect.
  // It is owned by the pointer event handlers below so the gesture
  // ownership check is synchronous within the same event cycle.

  // When the store's selected wallpaper changes from outside (e.g. a brand
  // new wallpaper arrived via SSE), the WallpaperCarousel's `initialIndex`
  // prop already follows it and re-aligns without animation. Nothing to do.

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
      swipeStartXRefOnDownRef.current = null;
      swipeStartYRefOnDownRef.current = null;
      voiceGestureActiveRef.current = false;
      voiceGesturePointerIdRef.current = null;
      // Notify hold-anywhere too — interactive regions cancel any in-flight hold.
      holdAnywhere.onPointerUp(event);
      return;
    }

    // Always remember the original pointerDown coords so the carousel
    // can take over after a swipe-cancel from hold-anywhere, or so voice
    // gesture can know its origin.
    swipeStartXRefOnDownRef.current = event.clientX;
    swipeStartYRefOnDownRef.current = event.clientY;

    // First, attempt to claim the gesture for hold-anywhere (any non-control
    // region of the wallpaper canvas is a valid hold-anywhere zone).
    // The hook will start a 2s timer internally. If the user keeps holding,
    // it will escalate into a recorder start + halo appearance.
    const claimed = holdAnywhere.onPointerDown(event);
    if (claimed) {
      // Synchronous ownership claim — record both the flag and the
      // pointerId so subsequent pointerUp/pointerCancel can identify
      // this exact sequence even if onSwipeCancel has fired mid-stream.
      voiceGestureActiveRef.current = true;
      voiceGesturePointerIdRef.current = event.pointerId;
      // Defer carousel / vertical-gesture bookkeeping until we know
      // whether the hold resolves into a recording or a swipe.
      swipeStartYRef.current = null;
      swipeStartXRef.current = null;
      gestureConsumedRef.current = false;
      return;
    }

    swipeStartYRef.current = event.clientY;
    swipeStartXRef.current = event.clientX;
    gestureConsumedRef.current = false;
  };

  const handlePointerMoveCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    // Voice gesture owns this pointer → suppress carousel/vertical gestures
    // entirely (synchronous ref check; no useEffect dependency).
    if (
      voiceGestureActiveRef.current &&
      voiceGesturePointerIdRef.current === event.pointerId
    ) {
      // Still feed into the hook so its own swipe detection can cancel the
      // hold if the user clearly swipes. When it does, it will call
      // onSwipeCancel → we synchronously reset the ref below.
      holdAnywhere.onPointerMove(event);
      return;
    }

    // Always feed move events into hold-anywhere so its internal swipe
    // detector can cancel a pending hold. This is independent of the
    // carousel bookkeeping below.
    holdAnywhere.onPointerMove(event);
  };

  const handlePointerCancelCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    // pointerCancel: system interrupted the pointer sequence (Safari edge case,
    // device rotation, etc.). Treat as user_stop so the mic is released.
    if (
      voiceGestureActiveRef.current &&
      voiceGesturePointerIdRef.current === event.pointerId
    ) {
      holdAnywhere.onPointerUp(event);
      voiceGestureActiveRef.current = false;
      voiceGesturePointerIdRef.current = null;
      return;
    }
    // Even if voice never owned this sequence, still notify hold-anywhere
    // so its internal pointerId bookkeeping is consistent.
    holdAnywhere.onPointerUp(event);
  };

  const handlePointerUpCapture = (
    event: React.PointerEvent<HTMLDivElement>,
  ) => {
    // Voice owns this pointer → only handle stop, suppress carousel.
    if (
      voiceGestureActiveRef.current &&
      voiceGesturePointerIdRef.current === event.pointerId
    ) {
      holdAnywhere.onPointerUp(event);
      voiceGestureActiveRef.current = false;
      voiceGesturePointerIdRef.current = null;
      // Clear swipe refs to ensure no further pointerMove in the same
      // sequence can accidentally act as a carousel swipe.
      swipeStartXRef.current = null;
      swipeStartYRef.current = null;
      return;
    }

    // Not voice-owned (or pointerId mismatch): notify hold-anywhere on every
    // pointer up. If the hook was already cancelled by an internal swipe
    // detection, this is a no-op.
    holdAnywhere.onPointerUp(event);

    const startY = swipeStartYRef.current;
    const startX = swipeStartXRef.current;
    swipeStartYRef.current = null;
    swipeStartXRef.current = null;
    if (startY === null || startX === null) return;
    if (gestureConsumedRef.current) return;

    const deltaY = event.clientY - startY;
    const deltaX = event.clientX - startX;

    // ── Vertical gesture ───────────────────────────────────────────
    // Block all navigation gestures (vertical swipe, carousel paging) when no
    // generated wallpaper exists yet. The only active input on the pre-first-voice
    // page is the central recording button.
    if (!hasGeneratedWallpaper) return;

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

    // ── Horizontal gesture paging ────────────────────────────────────────
    // Carousel paging is owned by WallpaperCarousel itself. This handler
    // intentionally does nothing for horizontal gestures — the carousel's
    // own capture-phase pointer listeners run on the same root div and
    // will handle the drag, snap, and store commit.
  };

  return (
    <div
      className="wallpaper-root"
      onPointerDownCapture={handlePointerDownCapture}
      onPointerMoveCapture={handlePointerMoveCapture}
      onPointerUpCapture={handlePointerUpCapture}
      onPointerCancelCapture={handlePointerCancelCapture}
    >
      {/* True translate3d Apple-like carousel — every available wallpaper
          is a stable mounted page. Shown only after the first shared wallpaper
          is ready (post-first-voice). Before that, the root background shows the
          static base environment scene so the user never sees a black screen. */}
      {hasGeneratedWallpaper ? (
        <WallpaperCarousel
          ref={carouselRef}
          pages={carouselPages}
          initialIndex={safeCarouselIndex}
          enabled={!isSubjectInteractionActive && uiMode === "wallpaper" && !showAssets}
          onPageSettled={(visualIndex) => {
            const page = carouselPages[visualIndex];
            if (!page) return;
            setSelectedRevision(page.dayIndex, page.wallpaperIndex);
            console.log(
              "[Carousel] settled →",
              visualIndex,
              "day=",
              page.dayIndex,
              "wall=",
              page.wallpaperIndex,
            );
          }}
          onDrag={() => {
            // While dragging, suppress motion layers to save GPU.
          }}
          onDragChange={(active) => {
            setIsSubjectInteractionActive((prev) => prev || active);
          }}
        />
      ) : (
        /* Pre-first-voice: show the static base environment scene.
           The ChatOverlay's CentralRecordingButton is the only active
           interaction; Hold Anywhere is blocked by enabled=false. */
        <div
          className="absolute inset-0 bg-cover bg-center bg-no-repeat"
          style={{ backgroundImage: `url(${resolveApiAssetUrl(STATIC_BASE_SCENE_PATH)})` }}
          aria-hidden="true"
        />
      )}
      <AtmosphereOverlay
        motionPaused={isSubjectInteractionActive}
        cameraPositionX={cameraPositionX}
      />
      <PetalMotionLayer
        hidden={foregroundHidden}
        motionPaused={foregroundHidden || isSubjectInteractionActive}
      />
      {/* SubjectLiftLayer intentionally not rendered in this build.
          The voice gesture is now "Hold Anywhere" (see useHoldAnywhereRecorder).
          The component file remains dormant for future restoration. */}
      <ChatOverlay />

      {!showAssets &&
      uiMode === "wallpaper" &&
      selectedWallpaper &&
      focusMode === "balanced" ? (
        <PanoramaHoldControls
          position={cameraPositionX}
          disabled={isSubjectInteractionActive}
          onPositionChange={setCameraPositionX}
          // NOTE: onShortPress is intentionally omitted. Arrow tap must NOT
          // navigate wallpaper revisions — horizontal swipe is the only
          // revision navigation mechanism.
        />
      ) : null}

      {/* Bottom pagination dots — visible when there are 2+ real wallpapers */}
      {!showAssets &&
      uiMode === "wallpaper" &&
      availableWallpapers.length >= 2 ? (
        (() => {
          const total = availableWallpapers.length;
          // availableWallpapers is latest → oldest (newest first);
          // visualIndex flips so oldest sits at the left, newest at the right.
          const visualIndex = total - 1 - currentPageIndex;
          const WINDOW_SIZE = 5;
          let windowStart: number;
          if (total <= WINDOW_SIZE) {
            windowStart = 0;
          } else {
            windowStart = Math.max(0, Math.min(visualIndex - 2, total - WINDOW_SIZE));
          }
          const windowEnd = Math.min(total, windowStart + WINDOW_SIZE);
          return (
            <div
              className="pointer-events-none absolute left-0 right-0 z-40 flex justify-center"
              style={{ bottom: "calc(env(safe-area-inset-bottom, 0px) + 5rem)" }}
              aria-hidden
            >
              <div className="flex items-center gap-2 rounded-full bg-black/25 py-1.5 px-3 shadow-sm backdrop-blur-sm">
                {Array.from({ length: windowEnd - windowStart }, (_, offset) => {
                  const i = windowStart + offset;
                  const isActive = i === visualIndex;
                  return (
                    <div
                      key={i}
                      className={`h-2 w-2 rounded-full transition-all duration-200 ${
                        isActive
                          ? "bg-white/95 scale-110"
                          : "bg-white/40"
                      }`}
                    />
                  );
                })}
              </div>
            </div>
          );
        })()
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

      {/* Hold-Anywhere voice recording halo.
          Viewport-anchored so it is independent of the carousel transform. */}
      {haloVisible ? (
        <RecordingHalo
          clientX={holdAnywhere.state.clientX}
          clientY={holdAnywhere.state.clientY}
          phase={haloPhase}
        />
      ) : null}
    </div>
  );
}
