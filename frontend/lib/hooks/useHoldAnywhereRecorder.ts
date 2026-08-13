"use client";

/**
 * useHoldAnywhereRecorder
 *
 * New "Hold Anywhere to Record" voice interaction.
 *
 * Design (matches the agreed spec):
 *   - PointerDown on wallpaper canvas → 2-second hold timer starts.
 *   - During the 2s candidate window: gesture is not committed.
 *   - Clear horizontal swipe → gesture arbitration → carousel owns.
 *   - 2s timer fires + pointer still held → recorder starts, halo appears.
 *   - PointerUp → recorder stops, halo disappears.
 *
 * Key behaviours:
 *   - Works on ANY wallpaper region (no subject hit-test).
 *   - Reuses the existing useWallpaperVoiceEditRecorder (same pipeline,
 *     same processing, same generation — just a different trigger + visual).
 *   - Does NOT interfere with SubjectLiftLayer's existing pointer listeners.
 *     WallpaperStage owns gesture arbitration and calls into this hook.
 *   - A concurrent pointerHeldRef from SubjectLift blocks hold-anywhere.
 *
 * State machine (simplified, no armed_lifted / cutout complexity):
 *   idle → hold_pending → recording → idle
 */

import { useCallback, useRef, useState } from "react";
import { useWallpaperVoiceEditRecorder } from "@/lib/hooks/useWallpaperVoiceEditRecorder";
import { useSceneStore } from "@/lib/hooks/useSceneStore";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";

/** Time from pointerDown to recorder-start / halo-appearance. */
export const HOLD_TO_RECORD_MS = 2000;

/** Minimum horizontal drag distance before we declare a swipe intent
 *  and cancel the hold timer. Deliberately higher than MOVE_TOLERANCE_PX
 *  so natural finger jitter (5–15 px) never cancels a real hold. */
const SWIPE_HORIZONTAL_THRESHOLD_PX = 22;

/** The dominant-axis ratio required to consider movement a horizontal swipe
 *  rather than vertical / diagonal. */
const SWIPE_AXIS_RATIO = 1.3;

export type HoldAnywhereState = {
  status: "idle" | "hold_pending" | "recording";
  clientX: number;
  clientY: number;
  source: "hold_anywhere";
};

export type UseHoldAnywhereRecorderOptions = {
  /** Pointer is currently held by SubjectLift (subject_lift ownership). */
  subjectLiftHeld: boolean;
  /** Current UI mode — hold-anywhere only active in wallpaper mode. */
  uiMode: "wallpaper" | "white";
  /** Called when the user clearly swipes and the hold is cancelled,
   *  so the caller (WallpaperStage) can take over gesture ownership.
   *  Receives the pointerId so the caller can verify the sequence. */
  onSwipeCancel?: (pointerId: number) => void;
};

export type UseHoldAnywhereRecorderReturn = {
  state: HoldAnywhereState;
  /**
   * Called by WallpaperStage when the pointer goes down on a valid canvas region.
   * Returns true if this hook claimed the pointer for hold-anywhere.
   */
  onPointerDown: (e: React.PointerEvent<HTMLElement>) => boolean;
  /** Called by WallpaperStage on every pointerMove on the canvas. */
  onPointerMove: (e: React.PointerEvent<HTMLElement>) => void;
  /** Called by WallpaperStage on pointerUp / pointerCancel. */
  onPointerUp: (e: React.PointerEvent<HTMLElement>) => void;
};

/** Stable source identifier for this interaction. */
const HOLD_ANYWHERE_SOURCE = "hold_anywhere" as const;

export function useHoldAnywhereRecorder({
  subjectLiftHeld,
  uiMode,
  onSwipeCancel,
}: UseHoldAnywhereRecorderOptions): UseHoldAnywhereRecorderReturn {
  const { start, finishRecording } = useWallpaperVoiceEditRecorder();
  const relationshipId =
    useOnboardingStore((s) => s.userContext?.relationshipId ?? "") ?? "";

  const [state, setState] = useState<HoldAnywhereState>({
    status: "idle",
    clientX: 0,
    clientY: 0,
    source: HOLD_ANYWHERE_SOURCE,
  });

  // ── Refs (imperative, not reactive) ────────────────────────────────
  const pointerIdRef = useRef<number | null>(null);
  const startXRef = useRef<number>(0);
  const startYRef = useRef<number>(0);
  const holdTimerRef = useRef<number | null>(null);
  /** True once the hold timer has fired and recorder has started. */
  const recordingStartedRef = useRef(false);
  /** True if the user released (pointerUp / pointerCancel) between
   *  `beginRecording` being called and `start(ctx)` resolving. The
   *  resolved-state handler checks this to avoid setting state to
   *  "recording" (or keeping mic live) on a stale sequence. */
  const releasedDuringStartRef = useRef(false);
  /** Track the latest clientX/Y for accurate halo placement. */
  const latestXRef = useRef<number>(0);
  const latestYRef = useRef<number>(0);
  /** Cancels the hold timer and cleans up. */
  const cancelHold = useCallback(() => {
    if (holdTimerRef.current !== null) {
      clearTimeout(holdTimerRef.current);
      holdTimerRef.current = null;
    }
    recordingStartedRef.current = false;
  }, []);

  // ── Internal: actually start the recorder ──────────────────────────
  const beginRecording = useCallback(
    async (clientX: number, clientY: number) => {
      if (recordingStartedRef.current) return;
      // Mark "starting" so further pointerUp events treat us as live and
      // call finishRecording. Critical: this flip happens BEFORE `await
      // start(ctx)` so that a pointerUp during the async setup path
      // is recorded (releasedDuringStartRef) and the start resolves are
      // checked against the captured pointerId / sequence id.
      recordingStartedRef.current = true;

      const captureId = crypto.randomUUID();
      const sequenceIdAtStart = pointerIdRef.current;
      console.log("[HOLD_ANYWHERE] recording started", {
        captureId,
        sequenceIdAtStart,
        clientX,
        clientY,
      });

      const ctx = {
        captureId,
        relationshipId,
        isFromHistoricalPage: false,
        source: HOLD_ANYWHERE_SOURCE,
      } as const;

      const ok = await start(ctx);

      // After awaiting, the user may have released (pointerUp /
      // pointerCancel / swipe-cancel) before start resolved.
      // - If releasedDuringStartRef fires, the recorder should NOT keep
      //   the mic live; finishRecording has already been called by the
      //   pointerUp handler.
      // - sequenceIdAtStart mismatched means a fresh pointer sequence
      //   replaced ours; abandon this resolved start (it'll belong to
      //   the new sequence anyway because start() is keyed per-call, but
      //   the safest move is to just stop).
      // - If recorder fails to start, fall back to idle.
      if (releasedDuringStartRef.current) {
        console.log(
          "[HOLD_ANYWHERE] start resolved after release; stopping cleanly",
          { captureId },
        );
        releasedDuringStartRef.current = false;
        recordingStartedRef.current = false;
        // finishRecording is idempotent and safe to call; the upstream
        // pointerUp already triggered endRecording() which calls it once,
        // but that path may have early-returned if recordingStartedRef
        // was true before this async resolve. Either way, calling
        // finishRecording twice is harmless (track.stop() is idempotent).
        finishRecording();
        setState({
          status: "idle",
          clientX,
          clientY,
          source: HOLD_ANYWHERE_SOURCE,
        });
        return;
      }
      if (pointerIdRef.current !== sequenceIdAtStart) {
        console.log(
          "[HOLD_ANYWHERE] start resolved for stale sequence; abandoning",
          { captureId },
        );
        recordingStartedRef.current = false;
        finishRecording();
        return;
      }
      if (!ok) {
        console.warn(
          "[HOLD_ANYWHERE] recorder start failed, falling back to idle",
        );
        recordingStartedRef.current = false;
        setState({
          status: "idle",
          clientX,
          clientY,
          source: HOLD_ANYWHERE_SOURCE,
        });
        return;
      }

      setState({
        status: "recording",
        clientX,
        clientY,
        source: HOLD_ANYWHERE_SOURCE,
      });
    },
    [relationshipId, start, finishRecording],
  );

  // ── Internal: stop the recorder ─────────────────────────────────────
  const endRecording = useCallback(
    (_reason: string) => {
      if (!recordingStartedRef.current) return;
      recordingStartedRef.current = false;
      cancelHold();
      console.log("[HOLD_ANYWHERE] recording stopped");
      finishRecording();
      setState({
        status: "idle",
        clientX: latestXRef.current,
        clientY: latestYRef.current,
        source: HOLD_ANYWHERE_SOURCE,
      });
    },
    [cancelHold, finishRecording],
  );

  // ── Public: pointerDown ─────────────────────────────────────────────
  /**
   * Called from WallpaperStage's pointerDown. Returns true if this hook
   * has claimed the pointer (gesture → hold_anywhere candidate). Returns
   * false so WallpaperStage knows to NOT start the carousel either.
   */
  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>): boolean => {
      // Block if SubjectLift already owns the pointer.
      if (subjectLiftHeld) return false;
      // Only in wallpaper mode.
      if (uiMode !== "wallpaper") return false;
      // Only primary pointer.
      if (!e.isPrimary) return false;
      // Ignore mouse button ≠ left.
      if (e.pointerType === "mouse" && e.button !== 0) return false;
      // Ignore if already recording from somewhere else.
      if (recordingStartedRef.current) return false;

      pointerIdRef.current = e.pointerId;
      startXRef.current = e.clientX;
      startYRef.current = e.clientY;
      latestXRef.current = e.clientX;
      latestYRef.current = e.clientY;

      // Enter hold_pending.
      setState({
        status: "hold_pending",
        clientX: e.clientX,
        clientY: e.clientY,
        source: HOLD_ANYWHERE_SOURCE,
      });

      // Start 2-second timer.
      holdTimerRef.current = window.setTimeout(() => {
        if (pointerIdRef.current !== e.pointerId) return;
        if (recordingStartedRef.current) return;
        void beginRecording(e.clientX, e.clientY);
      }, HOLD_TO_RECORD_MS);

      console.log("[HOLD_ANYWHERE] pointer_down", {
        clientX: e.clientX,
        clientY: e.clientY,
        pointerType: e.pointerType,
        pointerId: e.pointerId,
      });

      return true;
    },
    [subjectLiftHeld, uiMode, beginRecording],
  );

  // ── Public: pointerMove ─────────────────────────────────────────────
  /** Called from WallpaperStage's pointerMove (only when not swiping). */
  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (pointerIdRef.current === null) return;
      if (e.pointerId !== pointerIdRef.current) return;

      latestXRef.current = e.clientX;
      latestYRef.current = e.clientY;

      // Keep halo position updated during hold_pending (for instant visual feedback
      // when 2s fires even if finger drifted slightly).
      if (state.status === "hold_pending") {
        setState((prev) =>
          prev.status === "hold_pending"
            ? { ...prev, clientX: e.clientX, clientY: e.clientY }
            : prev,
        );
      }

      // Gesture arbitration: cancel hold if user clearly swipes horizontally.
      // Use the START position as anchor (not accumulated delta), matching
      // the original MOVE_TOLERANCE logic so the user can "escape" a hold
      // they started by dragging.
      const dx = e.clientX - startXRef.current;
      const dy = e.clientY - startYRef.current;

      if (
        Math.abs(dx) > SWIPE_HORIZONTAL_THRESHOLD_PX &&
        Math.abs(dx) > Math.abs(dy) * SWIPE_AXIS_RATIO
      ) {
        console.log("[HOLD_ANYWHERE] swipe detected, cancelling hold", {
          dx,
          dy,
        });
        cancelHold();
        pointerIdRef.current = null;
        setState({ status: "idle", clientX: e.clientX, clientY: e.clientY, source: HOLD_ANYWHERE_SOURCE });
        // Notify the caller so it can take over gesture ownership
        // (carousel paging) using the original pointerDown coordinates.
        onSwipeCancel?.(e.pointerId);
      }
    },
    [state.status, cancelHold, onSwipeCancel],
  );

  // ── Public: pointerUp ───────────────────────────────────────────────
  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (pointerIdRef.current !== e.pointerId) return;
      if (pointerIdRef.current === null) return;

      console.log("[HOLD_ANYWHERE] pointer_up", {
        wasRecording: recordingStartedRef.current,
        status: state.status,
      });

      const wasRecording = recordingStartedRef.current;
      cancelHold();
      pointerIdRef.current = null;

      if (wasRecording) {
        // The recorder starts asynchronously in beginRecording. If the
        // user releases BEFORE start(ctx) resolves, mark this so the
        // resolved-state path stops the recorder cleanly instead of
        // setting state to "recording" and leaving the mic live.
        releasedDuringStartRef.current = true;
        // Try to stop now in case the recorder has already initialised
        // track state (some browsers begin mic capture mid-await). If
        // start() hasn't resolved yet, finishRecording is a safe no-op
        // until it does.
        endRecording("user_stop");
      } else {
        // Cancelled hold (finger lifted before 2s, or swipe cancelled).
        setState({
          status: "idle",
          clientX: e.clientX,
          clientY: e.clientY,
          source: HOLD_ANYWHERE_SOURCE,
        });
      }
    },
    [state.status, cancelHold, endRecording],
  );

  return { state, onPointerDown, onPointerMove, onPointerUp };
}
