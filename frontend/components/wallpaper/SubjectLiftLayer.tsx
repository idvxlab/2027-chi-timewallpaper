"use client";

/**
 * SubjectLiftLayer — transparent overlay on top of the wallpaper.
 *
 * State machine integration:
 *
 *   idle
 *     │ pointerdown (on partner region, ~500ms)
 *     ▼
 *   pressing ──extraction success──▶ waiting_for_release
 *     │                                     │
 *     │ extraction fail                    │ pointerup (first release)
 *     ▼                                     ▼
 *   idle                              armed_lifted (10 second window)
 *                                          │
 *                                          │ second tap on partner → recording
 *                                          │ or 10s timeout
 *                                          ▼
 *                                        releasing ──240ms──▶ idle
 *
 *   recording
 *     │ click anywhere (subject / background / other person) → user_stop
 *     │ recorder.onerror / MAX_TIMEOUT                       → recorder_*_stop
 *     ▼
 *   releasing ──240ms──▶ idle
 *
 * Key behaviors:
 *   1. Only partner region can be lifted (based on viewerRole).
 *   2. First pointerup after extraction success enters armed_lifted.
 *   3. The 10-second armed timer starts ONLY when BOTH extraction has
 *      succeeded AND the first pointerup has occurred.
 *   4. Recording starts on second tap (subject), not on first release.
 *   5. While recording, the lifted subject gently pulses up-and-down
 *      (~10s cycle, no opacity flicker, no scale flicker).
 *   6. Click anywhere on the wallpaper while recording stops the
 *      recorder and immediately drops the subject back to its anchor
 *      position. The audio blob is forwarded to the existing
 *      ASR / agent / image-generation pipeline via processRecording.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { imageBBoxToClientRect, _setAlphaMask } from "@/lib/subjectGeometry";
import { getAlphaRingMarkup } from "@/lib/hooks/subjectAlphaRing";
import {
  getSelectedWallpaper,
  useSceneStore,
} from "@/lib/hooks/useSceneStore";
import {
  useSubjectLift,
  DEBUG_SUBJECT_LIFT_OVERLAY,
  type LiftState,
} from "@/lib/hooks/useSubjectLift";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { useWallpaperVoiceEditRecorder } from "@/lib/hooks/useWallpaperVoiceEditRecorder";
import { getWallpaperDisplayGeometry } from "@/lib/wallpaperDisplayGeometry";

type NaturalSize = { width: number; height: number };

type DisplayedCutout = {
  cutoutUrl: string;
  bbox: { x: number; y: number; width: number; height: number };
  region: "upper" | "lower";
  id: number;
  loadFailed: boolean;
};

// How long the click that started recording is suppressed from being
// interpreted as a stop click. Must match useSubjectLift constant.
const REC_START_STOP_GUARD_MS = 300;

type SubjectLiftLayerProps = {
  onInteractionActiveChange?: (active: boolean) => void;
};

export function SubjectLiftLayer({
  onInteractionActiveChange,
}: SubjectLiftLayerProps) {
  // ── Wallpaper state ──────────────────────────────────────────────────
  const activeWallpaperUrl = useSceneStore(
    (s) => getSelectedWallpaper(s)?.imageUrl || "",
  );
  const focusMode = useSceneStore((s) => s.focusMode);
  const [imageSize, setImageSize] = useState<NaturalSize | null>(null);

  useEffect(() => {
    if (!activeWallpaperUrl) {
      setImageSize(null);
      return;
    }
    const img = new window.Image();
    img.onload = () =>
      setImageSize({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => setImageSize(null);
    img.src = activeWallpaperUrl;
  }, [activeWallpaperUrl]);

  // ── Subject lift hook ────────────────────────────────────────────────
  const {
    state,
    handlers,
    canLift,
    canLiftChecks,
    viewerRole,
    startRecording,
    stopRecording,
    registerRecorderBridge,
  } = useSubjectLift({ imageSize, focusMode });
  const interactionActive =
    state.status !== "idle" && state.status !== "error";

  useEffect(() => {
    onInteractionActiveChange?.(interactionActive);
    return () => onInteractionActiveChange?.(false);
  }, [interactionActive, onInteractionActiveChange]);

  // ── Viewer role ────────────────────────────────────────────────────
  const role = useOnboardingStore((s) => s.role);
  const currentViewerRole: "child" | "elder" =
    role === "elder" ? "elder" : "child";

  // ── Recorder ───────────────────────────────────────────────────────
  const { status: voiceStatus, start: recorderStart, finishRecording: recorderStop } =
    useWallpaperVoiceEditRecorder();

  // Wire recorder hooks into the subject-lift hook so it can fire them.
  useEffect(() => {
    registerRecorderBridge(
      (vr) => {
        recorderStart(vr);
        return true;
      },
      () => {
        recorderStop();
      },
    );
  }, [registerRecorderBridge, recorderStart, recorderStop]);

  // ── Container bounding rect ────────────────────────────────────────
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [wallpaperRect, setWallpaperRect] = useState<DOMRect | null>(null);
  const [wallpaperPositionX, setWallpaperPositionX] = useState(0.5);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const overlayRect = el.getBoundingClientRect();
      const display = getWallpaperDisplayGeometry();
      setRect(overlayRect);
      setWallpaperRect(display?.rect ?? overlayRect);
      setWallpaperPositionX(display?.objectPositionX ?? 0.5);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    const motionSurface = document.querySelector<HTMLElement>(
      "[data-wallpaper-motion-surface]",
    );
    if (motionSurface) ro.observe(motionSurface);
    window.addEventListener("scroll", update, { passive: true });
    return () => {
      ro.disconnect();
      window.removeEventListener("scroll", update);
    };
  }, []);

  // ── Update rect when entering armed/pressing states ──────────────────
  useEffect(() => {
    if (
      state.status === "armed_lifted" ||
      state.status === "recording" ||
      state.status === "releasing" ||
      state.status === "waiting_for_release" ||
      state.status === "pressing" ||
      state.status === "extracting"
    ) {
      const el = containerRef.current;
      if (el) {
        const overlayRect = el.getBoundingClientRect();
        const display = getWallpaperDisplayGeometry();
        setRect(overlayRect);
        setWallpaperRect(display?.rect ?? overlayRect);
        setWallpaperPositionX(display?.objectPositionX ?? 0.5);
      }
    }
  }, [state.status]);

  // ── Lifted cutout display state ─────────────────────────────────────
  const [displayed, setDisplayed] = useState<DisplayedCutout | null>(null);

  useEffect(() => {
    if (state.status === "armed_lifted") {
      const s = state as Extract<LiftState, { status: "armed_lifted" }>;
      console.log("[SubjectLiftLayer] entering armed_lifted, rendering cutout", {
        cutoutUrl: s.cutoutUrl.substring(0, 50) + "...",
        bbox: s.bbox,
        region: s.region,
      });
      setDisplayed({
        cutoutUrl: s.cutoutUrl,
        bbox: s.bbox,
        region: s.region,
        id: Date.now(),
        loadFailed: false,
      });
    } else if (state.status === "recording") {
      // Keep the displayed cutout during recording. The visual keeps
      // the cutout url/bbox the same so the pulse animation runs on it.
      const s = state as Extract<LiftState, { status: "recording" }>;
      setDisplayed((prev) =>
        prev
          ? {
              ...prev,
              cutoutUrl: s.cutoutUrl,
              bbox: s.bbox,
              region: s.region,
            }
          : {
              cutoutUrl: s.cutoutUrl,
              bbox: s.bbox,
              region: s.region,
              id: Date.now(),
              loadFailed: false,
            },
      );
    } else if (state.status === "releasing") {
      // Keep displayed briefly for fall-back animation.
      const s = state as Extract<LiftState, { status: "releasing" }>;
      setDisplayed((prev) =>
        prev
          ? { ...prev, cutoutUrl: s.cutoutUrl, bbox: s.bbox, id: Date.now() }
          : prev,
      );
    } else if (state.status === "idle" || state.status === "error") {
      setDisplayed(null);
    }
  }, [state]);

  // ── Background dim ─────────────────────────────────────────────────
  const isLifted =
    state.status === "armed_lifted" ||
    state.status === "recording" ||
    state.status === "waiting_for_release" ||
    state.status === "releasing";
  const backgroundDim = isLifted ? 0.97 : 1;

  // Track the recording-start timestamp locally so the click that started
  // recording is not interpreted as a stop click. The hook also guards.
  const useSubjectLiftStartedAtRef = useRef<number>(0);

  // ── Click handling ──────────────────────────────────────────────────
  // armed_lifted + click subject → startRecording
  // recording + click anywhere    → stopRecording
  //
  // The SubjectLiftLayer interaction layer receives pointer events on the
  // wallpaper area. We use a single pointerdown listener and decide based
  // on the current lift state whether to start, stop, or ignore.
  const handleInteractionPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      // Defer all heavy work to handlers from useSubjectLift for the long-
      // press / extraction path.
      handlers.onPointerDown(e);

      // Click-anywhere-while-recording → stop
      if (state.status === "recording") {
        // If the very same click that started recording bubbles here
        // within the guard window, ignore it.
        const sinceStart = Date.now() - useSubjectLiftStartedAtRef.current;
        if (sinceStart < REC_START_STOP_GUARD_MS) {
          console.log(
            "[SubjectLiftLayer] stop suppressed by start-guard",
            { sinceStart },
          );
          return;
        }
        console.log(
          "[wallpaper_recording_interaction]",
          {
            action: "stop",
            trigger: "any",
            voiceStatus,
            pointerId: e.pointerId,
          },
        );
        stopRecording("user_stop");
        // Suppress the browser-emitted click that follows pointerdown.
        e.stopPropagation();
        e.preventDefault();
        return;
      }
    },
    [handlers, state.status, voiceStatus, stopRecording],
  );

  useEffect(() => {
    if (state.status === "recording") {
      useSubjectLiftStartedAtRef.current = Date.now();
    }
  }, [state.status]);

  // ── Pointer handlers ────────────────────────────────────────────────
  const handleInteractionPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => handlers.onPointerMove(e),
    [handlers],
  );
  const handleInteractionPointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => handlers.onPointerUp(e),
    [handlers],
  );
  const handleInteractionPointerCancel = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => handlers.onPointerCancel(e),
    [handlers],
  );

  // ── Subject-tap: used to start recording on armed_lifted ─────────────
  // We bind the onClick of the lifted subject to startRecording directly
  // (see LiftedSubjectVisual). For the surrounding transparent area we
  // already capture pointerdown via handleInteractionPointerDown above.
  const handleSubjectClickStart = useCallback(
    (e: React.MouseEvent | React.PointerEvent) => {
      if (state.status !== "armed_lifted") return;
      e.stopPropagation();
      e.preventDefault();
      console.log(
        "[wallpaper_recording_interaction]",
        {
          action: "start",
          trigger: "subject",
          voiceStatus,
          pointerId: "click",
        },
      );
      startRecording(currentViewerRole);
    },
    [state.status, startRecording, currentViewerRole, voiceStatus],
  );

  // ── Recording state transitions ────────────────────────────────────
  // When recorder reports transcribing/editing, we treat that as "recorder
  // finished the audio"; the subject has already begun falling back.
  // If we somehow reach a non-idle status without going through releasing
  // (e.g. recorder error), force a release so the subject never gets stuck.
  useEffect(() => {
    if (
      voiceStatus === "transcribing" ||
      voiceStatus === "editing" ||
      voiceStatus === "idle"
    ) {
      if (state.status === "recording") {
        // External recorder already finished; transition to releasing.
        console.log(
          "[SubjectLiftLayer] recorder finished externally, force releasing",
          { voiceStatus },
        );
        const s = state as Extract<LiftState, { status: "recording" }>;
        stopRecording(voiceStatus === "idle" ? "recorder_error" : "recorder_auto_stop");
      }
    }
  }, [voiceStatus, state, stopRecording]);

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div
      ref={containerRef}
      data-subject-lift-container
      className="absolute inset-0"
      style={{
        zIndex: 30,
        pointerEvents: "none",
        touchAction: "none",
        userSelect: "none",
        WebkitUserSelect: "none",
        WebkitTouchCallout: "none",
      }}
      onContextMenu={handlers.onContextMenu}
    >
      {/* Main interaction layer */}
      <div
        className="absolute inset-0"
        style={{ pointerEvents: "auto" }}
        onPointerDown={handleInteractionPointerDown}
        onPointerMove={handleInteractionPointerMove}
        onPointerUp={handleInteractionPointerUp}
        onPointerCancel={handleInteractionPointerCancel}
      />

      {/* Background dim */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          opacity: 1 - backgroundDim,
          transition: "opacity 220ms ease-out",
        }}
      />

      {/* Debug overlays */}
      {DEBUG_SUBJECT_LIFT_OVERLAY ? (
        <DebugRegions
          containerRect={rect}
          canLift={canLift}
          canLiftChecks={canLiftChecks}
          viewerRole={viewerRole}
        />
      ) : null}

      {/* Pressing ring */}
      {state.status === "pressing" && rect ? (
        <PressingRing state={state} containerRect={rect} />
      ) : null}

      {/* Extracting shimmer - show at press position */}
      {(state.status === "extracting" || state.status === "waiting_for_release") &&
      rect ? (
        <LoadingSparkle
          containerRect={rect}
          clientX={
            state.status === "extracting"
              ? (state as Extract<LiftState, { status: "extracting" }>).clientX
              : null
          }
          clientY={
            state.status === "extracting"
              ? (state as Extract<LiftState, { status: "extracting" }>).clientY
              : null
          }
        />
      ) : null}

      {/* Lifted cutout */}
      {displayed && rect && wallpaperRect && imageSize && !displayed.loadFailed ? (
        <LiftedSubjectVisual
          displayed={displayed}
          containerRect={rect}
          wallpaperRect={wallpaperRect}
          wallpaperPositionX={wallpaperPositionX}
          naturalSize={imageSize}
          isArmed={state.status === "armed_lifted"}
          isRecording={state.status === "recording"}
          isReleasing={state.status === "releasing"}
          onSubjectClickStart={handleSubjectClickStart}
          onAlphaMaskBuilt={(mask) => {
            _setAlphaMask(mask);
          }}
          onError={() =>
            setDisplayed((prev) =>
              prev ? { ...prev, loadFailed: true } : prev,
            )
          }
        />
      ) : null}
    </div>
  );
}

// ── Pressing ring ─────────────────────────────────────────────────────

type RingProps = {
  containerRect: DOMRect;
};

function PressingRing({
  state,
  containerRect,
}: RingProps & { state: Extract<LiftState, { status: "pressing" }> }) {
  const localX = state.clientX - containerRect.left;
  const localY = state.clientY - containerRect.top;
  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left: localX - 22,
        top: localY - 22,
        width: 44,
        height: 44,
        borderRadius: 999,
        background:
          "radial-gradient(circle, rgba(255,255,255,0) 55%, rgba(255,255,255,0.5) 75%, rgba(255,255,255,0) 100%)",
        animation: "subject-lift-pulse 480ms ease-out forwards",
      }}
    />
  );
}

// ── Loading sparkle ───────────────────────────────────────────────────

function LoadingSparkle({
  containerRect,
  clientX,
  clientY,
}: RingProps & { clientX: number | null; clientY: number | null }) {
  // If we have the press position, show sparkle there
  // Otherwise show at center
  const localX = clientX !== null
    ? clientX - containerRect.left
    : containerRect.width / 2;
  const localY = clientY !== null
    ? clientY - containerRect.top
    : containerRect.height / 2;

  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left: localX - 18,
        top: localY - 18,
        width: 36,
        height: 36,
        borderRadius: 999,
        background:
          "radial-gradient(circle, rgba(255,255,255,0.9) 0%, rgba(255,255,255,0) 70%)",
        animation: "subject-lift-loading 900ms ease-in-out infinite",
      }}
    />
  );
}

// ── Lifted subject visual ─────────────────────────────────────────────

function LiftedSubjectVisual({
  displayed,
  containerRect,
  wallpaperRect,
  wallpaperPositionX,
  naturalSize,
  isArmed,
  isRecording,
  isReleasing,
  onSubjectClickStart,
  onAlphaMaskBuilt,
  onError,
}: {
  displayed: DisplayedCutout;
  containerRect: DOMRect;
  wallpaperRect: DOMRect;
  wallpaperPositionX: number;
  naturalSize: NaturalSize;
  isArmed: boolean;
  isRecording: boolean;
  isReleasing: boolean;
  onSubjectClickStart: (e: React.MouseEvent | React.PointerEvent) => void;
  onAlphaMaskBuilt: (mask: {
    url: string;
    width: number;
    height: number;
    pixels: Uint8Array;
    bbox: { x: number; y: number; width: number; height: number };
    containerRect: DOMRect;
    naturalWidth: number;
    naturalHeight: number;
  }) => void;
  onError: () => void;
}) {
  // Convert bbox from source image coordinates to screen coordinates
  const screen = imageBBoxToClientRect({
    bbox: displayed.bbox,
    containerRect: wallpaperRect,
    naturalWidth: naturalSize.width,
    naturalHeight: naturalSize.height,
    objectPositionX: wallpaperPositionX,
  });

  console.log("[subject_lift_render]", {
    status: "rendering",
    region: displayed.region,
    bbox: displayed.bbox,
    screenRect: screen,
    naturalSize,
  });

  const left = screen.left - containerRect.left;
  const top = screen.top - containerRect.top;
  const width = screen.width;
  const height = screen.height;

  // ── Build a coarse alpha mask from the cutout image ──────────────────
  // This allows future alpha hit-tests to differentiate opaque pixels
  // from transparent background. Runs once per cutoutUrl.
  const maskBuiltRef = useRef<string | null>(null);
  useEffect(() => {
    if (maskBuiltRef.current === displayed.cutoutUrl) return;
    if (!displayed.cutoutUrl) return;
    const targetUrl = displayed.cutoutUrl;
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const W = 64;
        const H = Math.max(
          16,
          Math.round((img.naturalHeight / img.naturalWidth) * W),
        );
        const cnv = document.createElement("canvas");
        cnv.width = W;
        cnv.height = H;
        const ctx = cnv.getContext("2d");
        if (!ctx) return;
        ctx.drawImage(img, 0, 0, W, H);
        const data = ctx.getImageData(0, 0, W, H).data;
        const pixels = new Uint8Array(W * H);
        for (let i = 0; i < W * H; i++) {
          // alpha channel threshold = 128
          pixels[i] = data[i * 4 + 3] > 128 ? 1 : 0;
        }
        onAlphaMaskBuilt({
          url: targetUrl,
          width: W,
          height: H,
          pixels,
          bbox: displayed.bbox,
          containerRect,
          naturalWidth: naturalSize.width,
          naturalHeight: naturalSize.height,
        });
        maskBuiltRef.current = targetUrl;
      } catch {
        // Cross-origin taint, etc. — fall back to bbox-only hit-test.
      }
    };
    img.onerror = () => {
      // Best-effort; fall back to bbox-only hit-test.
    };
    img.src = displayed.cutoutUrl;
  }, [
    displayed.cutoutUrl,
    displayed.bbox,
    containerRect,
    naturalSize.width,
    naturalSize.height,
    onAlphaMaskBuilt,
  ]);

  const { markup: innerMarkup } = useMemo(
    () => getAlphaRingMarkup(displayed.cutoutUrl, width, height),
    [displayed.cutoutUrl, width, height],
  );

  // ── Layered transforms ──────────────────────────────────────────────
  // Outer (pos): absolute positioning of the cutout in viewport pixels.
  //               NEVER animated; it owns the bbox anchor.
  // Mid (anchor): holds the resting translateY (0) ↔ lifted (-8px) ↔
  //               release fallback. Smooth transitions on state change.
  // Inner (pulse): during recording, runs the gentle up-down loop.
  //
  // The clickable element is the OUTER container so the bbox hit-test is
  // stable across transform changes.
  const outerStyle: React.CSSProperties = {
    position: "absolute",
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    height: `${height}px`,
    zIndex: 45,
    // Clickable for stop/start when interacting with the lifted subject.
    // - armed_lifted: click subject → start recording.
    // - recording:    click subject → stop recording.
    // - otherwise:    pass through to background (handled by parent).
    pointerEvents: isArmed || isRecording ? "auto" : "none",
    willChange: "transform, opacity",
  };

  // Mid layer holds the lift (translateY -8px when armed/recording).
  const anchorTranslateY = isArmed || isRecording ? -8 : 0;
  const anchorScale = isArmed || isRecording ? 1.035 : 1;
  const anchorStyle: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    transform: `translateY(${anchorTranslateY}px) scale(${anchorScale})`,
    transition: isReleasing
      ? "transform 240ms cubic-bezier(0.2, 0.8, 0.2, 1)"
      : "transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1)",
    willChange: "transform",
    filter:
      "drop-shadow(0 8px 18px rgba(0,0,0,0.22)) drop-shadow(0 2px 4px rgba(0,0,0,0.15))",
  };

  // Inner layer: while recording, runs the up-down pulse via CSS
  // animation. While armed/releasing/idle, sits at 0.
  const innerStyle: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    transform: "translateY(0)",
    willChange: "transform",
    animation: isRecording
      ? "subject-lift-record-pulse 1100ms ease-in-out infinite alternate"
      : "none",
  };

  return (
    <>
      <style jsx global>{`
        @keyframes subject-lift-pulse {
          0% { transform: scale(0.6); opacity: 0; }
          30% { opacity: 1; }
          100% { transform: scale(1.6); opacity: 0; }
        }
        @keyframes subject-lift-loading {
          0%, 100% { transform: scale(0.8); opacity: 0.4; }
          50% { transform: scale(1.2); opacity: 1; }
        }
        @keyframes subject-lift-breathe {
          0%, 100% { transform: translateY(-6px) scale(1.025); }
          50% { transform: translateY(-8px) scale(1.03); }
        }
        @keyframes subject-lift-record-pulse {
          0% {
            transform: translateY(-2px);
          }
          100% {
            transform: translateY(8px);
          }
        }
      `}</style>

      <div
        key={displayed.id}
        style={outerStyle}
        onClick={(e) => {
          // Distinguish: click on subject → start/stop.
          if (isArmed) {
            e.stopPropagation();
            onSubjectClickStart(e);
          } else if (isRecording) {
            e.stopPropagation();
            // Click on the subject itself while recording also stops.
            // The parent layer's pointerdown handler normally intercepts
            // this; this is a defensive fallback for synthesised clicks
            // that bypass the pointerdown.
          }
        }}
        onPointerDown={(e) => {
          if (isArmed) {
            e.stopPropagation();
          } else if (isRecording) {
            e.stopPropagation();
          }
        }}
      >
        <div style={anchorStyle}>
          <div style={innerStyle}>
            <svg
              width={width}
              height={height}
              viewBox={`0 0 ${width} ${height}`}
              preserveAspectRatio="none"
              style={{ overflow: "visible", display: "block" }}
              dangerouslySetInnerHTML={{ __html: innerMarkup }}
            />
            {/* Actual cutout image */}
            <img
              src={displayed.cutoutUrl}
              alt="Lifted subject"
              onError={onError}
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                width: "100%",
                height: "100%",
                objectFit: "contain",
                pointerEvents: "none",
              }}
            />
          </div>
        </div>
      </div>
    </>
  );
}

// ── Debug regions ────────────────────────────────────────────────────

function DebugRegions({
  containerRect: _containerRect,
  canLift,
  canLiftChecks,
  viewerRole,
}: {
  containerRect: DOMRect | null;
  canLift: boolean;
  canLiftChecks: {
    uiModeIsWallpaper: boolean;
    isToday: boolean;
    insertReady: boolean;
    voiceIdle: boolean;
    hasGeneratedWallpaperUrl: boolean;
    urlIsFinal: boolean;
    focusBalanced: boolean;
    imageSizeReady: boolean;
  };
  viewerRole: "elder" | "child";
}) {
  return (
    <>
      <div
        className="absolute pointer-events-none"
        style={{
          left: "45%",
          top: "0%",
          width: "55%",
          height: "50%",
          background: "rgba(59,130,246,0.10)",
          border: "1px dashed rgba(59,130,246,0.55)",
        }}
      >
        <div
          className="absolute left-1 top-1 px-1.5 py-0.5 rounded text-[10px] font-mono"
          style={{ background: "rgba(59,130,246,0.85)", color: "white" }}
        >
          UPPER (child)
        </div>
      </div>
      <div
        className="absolute pointer-events-none"
        style={{
          left: "0%",
          top: "35%",
          width: "65%",
          height: "65%",
          background: "rgba(249,115,22,0.10)",
          border: "1px dashed rgba(249,115,22,0.55)",
        }}
      >
        <div
          className="absolute left-1 bottom-1 px-1.5 py-0.5 rounded text-[10px] font-mono"
          style={{ background: "rgba(249,115,22,0.85)", color: "white" }}
        >
          LOWER (elder)
        </div>
      </div>
      <div
        className="absolute pointer-events-none"
        style={{
          left: 8,
          top: 60,
          padding: "6px 10px",
          borderRadius: 8,
          background: canLift
            ? "rgba(34,197,94,0.85)"
            : "rgba(220,38,38,0.85)",
          color: "white",
          font: "11px/1.3 ui-monospace, monospace",
          maxWidth: "calc(100% - 16px)",
          whiteSpace: "pre-wrap",
        }}
      >
        {`viewerRole=${viewerRole}
canLift=${canLift}
uiMode? ${canLiftChecks.uiModeIsWallpaper}
isToday? ${canLiftChecks.isToday}
insertReady? ${canLiftChecks.insertReady}
voiceIdle? ${canLiftChecks.voiceIdle}
hasUrl? ${canLiftChecks.hasGeneratedWallpaperUrl}
urlIsFinal? ${canLiftChecks.urlIsFinal}
focusBalanced? ${canLiftChecks.focusBalanced}
imageSize? ${canLiftChecks.imageSizeReady}`}
      </div>
    </>
  );
}
