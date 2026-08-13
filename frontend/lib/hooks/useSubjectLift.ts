/**
 * useSubjectLift — hold-to-record Subject Lift.
 *
 * State machine:
 *
 *   idle
 *     │ pointerdown (on partner region)
 *     ▼
 *   pressing ──300ms──▶ extracting ──API ok──▶ armed_lifted
 *     │                      │                        │
 *     │                      └──API fail──────────────┘
 *     │ move>8px
 *     ▼
 *   idle
 *
 *   armed_lifted (cutout visible)
 *     │ auto-record after ~2s hold → recording
 *     │ (safety timeout fires only if pointer was released without proper cleanup)
 *     ▼
 *   releasing ──260ms──▶ idle
 *
 *   recording
 *     │ pointerup (user releases) → stop recording, releasing
 *     │ (silence/no_speech/max/error are IGNORED while pointer is held)
 *     ▼
 *   releasing ──260ms──▶ idle
 *
 * Permission logic (shared wallpaper fixed layout):
 *   upper = child, lower = elder
 *   elder viewer → partner = child → partnerRegion = upper
 *   child viewer → partner = elder → partnerRegion = lower
 *
 * Only the partner region can be lifted. The viewer's own region
 * (self) cannot be lifted.
 *
 * Interaction flow (hold-to-record):
 *   1. Long press partner region (~300ms)
 *   2. Extraction begins (cache hit, in-flight join, or live request)
 *   3. Cutout appears, lifts up
 *   4. User continues holding (~2s)
 *   5. Recording starts automatically
 *   6. User speaks while holding
 *   7. User releases → recording stops, cutout falls back
 *   8. Backend: ASR → edit with latest wallpaper → generate
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { extractSubject } from "@/lib/api";
import {
  clientPointToImagePoint,
  hitTestSubject,
  getPersonRoleByRegion,
  getPartnerRole,
  getPartnerRegion,
  isRegionLiftingAllowed,
  imageBBoxToClientRect,
  hitTestAlpha,
  DEBUG_SUBJECT_LIFT_LOOSE,
  type SubjectHit,
  type ViewerRole,
  type SubjectRegion,
  type PersonRole,
} from "@/lib/subjectGeometry";
import { type VoiceProcessingContext } from "@/lib/hooks/useWallpaperVoiceEditRecorder";
import { getWallpaperDisplayGeometry } from "@/lib/wallpaperDisplayGeometry";
import {
  getSelectedWallpaper,
  isLatestWallpaper,
  useSceneStore,
  getWallpaperInteractionMode,
  TODAY_INDEX,
  DAY_LABELS,
  type WallpapersByDay,
  type WallpaperItem,
} from "@/lib/hooks/useSceneStore";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { useWallpaperVoiceEditRecorder } from "@/lib/hooks/useWallpaperVoiceEditRecorder";

// ── Module-level cutout cache ──────────────────────────────────────────────────

export type SubjectExtractionResult = {
  cutoutUrl: string;
  bbox: { x: number; y: number; width: number; height: number };
  region: SubjectRegion;
};

export type SubjectCutoutCacheEntry = {
  result: SubjectExtractionResult;
  key: string;
  relationshipId: string;
  revisionId: string;
  wallpaperUrl: string;
  viewerRole: ViewerRole;
  region: SubjectRegion;
  createdAt: number;
};

/**
 * Module-level cache: keyed by buildSubjectCutoutCacheKey(...).
 * Each entry belongs to exactly one (relationshipId, wallpaperUrl, viewerRole,
 * personRole, region) tuple.
 */
const subjectCutoutCache = new Map<string, SubjectCutoutCacheEntry>();

/**
 * Module-level in-flight promise map: prevents duplicate POSTs for the same key.
 * Cleared when the promise resolves or rejects.
 */
const subjectCutoutInFlight = new Map<
  string,
  Promise<SubjectExtractionResult>
>();

/** Test-only: reset all module-level state between test runs. */
export function _resetCutoutCacheForTest() {
  subjectCutoutCache.clear();
  subjectCutoutInFlight.clear();
}

// ── Cache eviction constants ────────────────────────────────────────────
const MAX_SUBJECT_CUTOUT_CACHE_ENTRIES = 32;
const SUBJECT_CUTOUT_TTL_MS = 30 * 60 * 1000; // 30 minutes

/**
 * Evict expired and excess entries from the cutout cache.
 * - Always removes entries older than SUBJECT_CUTOUT_TTL_MS.
 * - Keeps cache size at or below MAX_SUBJECT_CUTOUT_CACHE_ENTRIES (oldest first).
 * - When currentRelationshipId is provided, also evicts entries from other relationships
 *   (e.g. after a relationship change or session exit).
 * - Never evicts in-flight entries.
 */
function pruneSubjectCutoutCache(currentRelationshipId?: string) {
  const now = Date.now();
  const inFlightKeys = new Set(subjectCutoutInFlight.keys());

  // Collect evictable entries (not in-flight).
  const entries = Array.from(subjectCutoutCache.entries()).filter(
    ([k]) => !inFlightKeys.has(k),
  );

  // Separate: expired, relationship-mismatch, and active.
  const expiredOrMismatch: typeof entries = [];
  const active: typeof entries = [];

  for (const entry of entries) {
    const tooOld = now - entry[1].createdAt > SUBJECT_CUTOUT_TTL_MS;
    const wrongRel =
      currentRelationshipId !== undefined &&
      entry[1].relationshipId !== currentRelationshipId;
    if (tooOld || wrongRel) {
      expiredOrMismatch.push(entry);
    } else {
      active.push(entry);
    }
  }

  // Evict all expired and wrong-relationship entries.
  for (const [key] of expiredOrMismatch) {
    subjectCutoutCache.delete(key);
  }

  // If after pruning expired/mismatched entries the cache would exceed capacity,
  // evict the oldest active entries so the write brings us back to exactly MAX.
  if (active.length >= MAX_SUBJECT_CUTOUT_CACHE_ENTRIES) {
    active.sort((a, b) => a[1].createdAt - b[1].createdAt);
    const toEvict = active.length - MAX_SUBJECT_CUTOUT_CACHE_ENTRIES + 1;
    for (let i = 0; i < toEvict; i++) {
      subjectCutoutCache.delete(active[i][0]);
    }
  }
}

/**
 * Normalize a wallpaper URL for cache key use by stripping volatile query/hash
 * parameters that don't affect the actual image content (e.g. timestamps, cache
 * busters, signed tokens). Falls back to the full URL if it looks stable.
 */
function normalizeWallpaperUrlForCache(url: string): string {
  try {
    const u = new URL(url);
    // Remove query params that are purely cache/timing fingerprints.
    // Keep the pathname since it contains the stable /generated/<id> identifier.
    const stripped = ["v", "t", "timestamp", "cb", "cachebust", "token", "sig"];
    for (const key of [...u.searchParams.keys()]) {
      if (stripped.includes(key.toLowerCase())) {
        u.searchParams.delete(key);
      }
    }
    return u.toString();
  } catch {
    return url;
  }
}

/**
 * Build a unique cache key for a partner cutout.
 * Uses revisionId (when available) as the stable identity of a wallpaper revision.
 * Falls back to a normalized wallpaperUrl for non-revisioned contexts.
 * revisionId is preferred because it is immune to URL-level cache-busting params.
 */
export function buildSubjectCutoutCacheKey(params: {
  relationshipId: string;
  revisionId: string;
  wallpaperUrl: string;
  viewerRole: ViewerRole;
  personRole: PersonRole;
  region: SubjectRegion;
}): string {
  const { relationshipId, revisionId, wallpaperUrl, viewerRole, personRole, region } =
    params;
  // revisionId is the primary stable identity; URL is a fallback for legacy callers.
  const wallpaperKey = revisionId || normalizeWallpaperUrlForCache(wallpaperUrl);
  return [relationshipId, wallpaperKey, viewerRole, personRole, region].join("::");
}

/**
 * Stable anchor point for the upper (child) region in normalized image space.
 * Matches the center of the strictUpper hit-test box.
 */
const UPPER_REGION_ANCHOR = { normalizedX: 0.76, normalizedY: 0.235 };

/**
 * Stable anchor point for the lower (elder) region in normalized image space.
 * Matches the center of the strictLower hit-test box.
 */
const LOWER_REGION_ANCHOR = { normalizedX: 0.275, normalizedY: 0.71 };

/**
 * Thrown when a prefetched cutout result is stale — the wallpaper,
 * version, or relationship has changed since the request started.
 * Callers must NOT treat this as a valid cutout result.
 */
export class StaleSubjectCutoutError extends Error {
  readonly #key: string;
  readonly #reasons: string[];

  constructor(key: string, reasons: string[]) {
    super(`StaleSubjectCutoutError: ${key} [${reasons.join(", ")}]`);
    this.name = "StaleSubjectCutoutError";
    this.#key = key;
    this.#reasons = reasons;
  }

  get key(): string {
    return this.#key;
  }

  get reasons(): string[] {
    return [...this.#reasons];
  }
}

/**
 * Get the stable anchor point for a given region.
 * Used by silent prefetch to request the partner cutout without a real click.
 */
function getRegionAnchor(region: SubjectRegion): { normalizedX: number; normalizedY: number } {
  return region === "upper" ? UPPER_REGION_ANCHOR : LOWER_REGION_ANCHOR;
}

/**
 * Core helper: fetch-or-cache a partner cutout for a given cache key.
 *
 * Behavior:
 *  1. Return cached result immediately if present.
 *  2. Join in-flight promise if another request for the same key is running.
 *  3. Otherwise, POST to extractSubject and store the promise.
 *  4. On success: validate, then write to module cache (stale guard).
 *  5. On failure: do NOT cache; allow retry on next long-press.
 *
 * This function is called by both the silent prefetch effect and the
 * long-press runExtraction path, ensuring exactly one POST per key.
 */
async function getOrRequestPartnerCutout(params: {
  relationshipId: string;
  revisionId: string;
  wallpaperUrl: string;
  viewerRole: ViewerRole;
  personRole: PersonRole;
  region: SubjectRegion;
  normalizedX: number;
  normalizedY: number;
}): Promise<SubjectExtractionResult> {
  const key = buildSubjectCutoutCacheKey(params);

  // 1. Cache hit — prune before returning to keep Map bounded.
  pruneSubjectCutoutCache(params.relationshipId);
  const cached = subjectCutoutCache.get(key);
  if (cached) {
    console.log("[SUBJECT_CUTOUT_CACHE] action=hit", {
      key,
      viewerRole: params.viewerRole,
      personRole: params.personRole,
      region: params.region,
      revisionId: params.revisionId || "url_fallback",
    });
    return cached.result;
  }

  // 2. In-flight join — MUST check and set BEFORE the await to prevent
  // race conditions when two callers invoke simultaneously.
  const existingInFlight = subjectCutoutInFlight.get(key);
  if (existingInFlight) {
    console.log("[SUBJECT_CUTOUT_CACHE] action=inflight_join", {
      key,
      revisionId: params.revisionId || "url_fallback",
    });
    const result = await existingInFlight;
    return result;
  }

  // 3. Fresh request — build the promise, store it synchronously, THEN await.
  // This ensures a second synchronous caller sees the in-flight entry immediately.
  console.log("[SUBJECT_CUTOUT_CACHE] action=miss", {
    key,
    viewerRole: params.viewerRole,
    personRole: params.personRole,
    region: params.region,
    revisionId: params.revisionId || "url_fallback",
  });

  const promise = (async () => {
    try {
      const apiResult = await extractSubject({
        imageUrl: params.wallpaperUrl,
        normalizedX: params.normalizedX,
        normalizedY: params.normalizedY,
        region: params.region,
        viewerRole: params.viewerRole,
      });

      if (!apiResult || !apiResult.cutoutUrl || !apiResult.bbox) {
        throw new Error("Invalid extraction result: missing cutoutUrl or bbox");
      }

      const result: SubjectExtractionResult = {
        cutoutUrl: apiResult.cutoutUrl,
        bbox: apiResult.bbox,
        region: params.region,
      };

      console.log("[subject_prefetch] action=success", {
        key,
        cutoutUrl: apiResult.cutoutUrl.substring(0, 50) + "...",
        bbox: apiResult.bbox,
      });

      return result;
    } catch (err) {
      console.log("[subject_prefetch] action=failed", {
        key,
        error: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  })();

  // Set in-flight BEFORE awaiting — this is the critical ordering for dedup.
  subjectCutoutInFlight.set(key, promise);

  // Await and clean up in-flight in all cases (success, stale, or error).
  let result: SubjectExtractionResult;
  try {
    result = await promise;
  } catch (err) {
    subjectCutoutInFlight.delete(key);
    throw err;
  }
  subjectCutoutInFlight.delete(key);

  // 4. Stale guard: re-read current store values to verify the wallpaper,
  // relationship, viewer identity, and interaction stage have not changed
  // since the request started.
  // URL is the primary identity — if it matches, the wallpaper content is the same.
  const currentUrl = useSceneStore.getState().generatedWallpaperUrl;
  const currentDayIndex = useSceneStore.getState().currentDayIndex;
  const currentWallpaperIndex = useSceneStore.getState().currentWallpaperIndex;
  const currentWallpapersByDay = useSceneStore.getState().wallpapersByDay;
  const currentInsertStatus = useSceneStore.getState().initialInsertStatus;
  const currentRelationshipId =
    useOnboardingStore.getState().userContext?.relationshipId ?? "";
  const currentRole = useOnboardingStore.getState().role;
  const currentViewerRole: ViewerRole =
    currentRole === "elder" || currentRole === "child" ? currentRole : "elder";
  const currentInteractionMode = getWallpaperInteractionMode(useSceneStore.getState());

  const reasons: string[] = [];

  // revisionId mismatch = wallpaper revision changed (strongest signal)
  const currentRevisionId =
    getSelectedWallpaper(useSceneStore.getState())?.revisionId ?? "";
  if (currentRevisionId && currentRevisionId !== params.revisionId) {
    reasons.push("revision_mismatch");
  }
  // URL mismatch as a secondary check (handles URL-only fallback path)
  if (currentUrl !== params.wallpaperUrl) {
    reasons.push("url_mismatch");
  }
  if (currentRelationshipId !== params.relationshipId) {
    reasons.push("relationship_mismatch");
  }
  if (currentViewerRole !== params.viewerRole) {
    reasons.push("viewer_role_mismatch");
  }
  if (getPartnerRole(currentViewerRole) !== params.personRole) {
    reasons.push("person_role_mismatch");
  }
  if (getPartnerRegion(currentViewerRole) !== params.region) {
    reasons.push("region_mismatch");
  }
  // Historical wallpapers: removed isLatestWallpaper check to allow Subject Lift on any revision.
  // The cutout comes from the currently visible wallpaper; voice generation always
  // uses latest shared wallpaper on the backend (no frontend change needed).
  if (currentInsertStatus !== "ready") {
    reasons.push("insert_not_ready");
  }
  // Allow caching on historical wallpapers even when not in subject_lift mode.
  // The stale guard for interaction mode is overly restrictive for historical pages.

  if (reasons.length > 0) {
    console.log("[SUBJECT_CUTOUT_CACHE] action=stale_ignored", {
      key,
      staleReason: reasons,
      currentRevisionId,
      paramsRevisionId: params.revisionId,
    });
    throw new StaleSubjectCutoutError(key, reasons);
  }

  // Prune before write to keep the Map bounded.
  pruneSubjectCutoutCache(params.relationshipId);

  // Write to module cache
  subjectCutoutCache.set(key, {
    result,
    key,
    relationshipId: params.relationshipId,
    revisionId: params.revisionId,
    wallpaperUrl: params.wallpaperUrl,
    viewerRole: params.viewerRole,
    region: params.region,
    createdAt: Date.now(),
  });

  return result;
}

// ── useSubjectLift ─────────────────────────────────────────────────────────────

// Long press threshold: 300ms (shortened for faster response)
const LONG_PRESS_MS = 300;
// Movement tolerance during long press
const MOVE_TOLERANCE_PX = 8;
// How long an armed subject stays lifted before auto-releasing (10 seconds)
const ARMED_TIMEOUT_MS = 10_000;
// Retry delay when recorder start fails transiently (e.g. capture_busy transition)
const AUTO_RECORD_RETRY_DELAY_MS = 800;
// Maximum transient retries before giving up (0 = no retry)
const AUTO_RECORD_MAX_RETRIES = 1;
// How long to show the releasing animation
const RELEASING_DURATION_MS = 260;
// Minimum gap between start-recording event and any stop-recording trigger
// to prevent the same pointer session from accidentally triggering stop.
const REC_START_STOP_GUARD_MS = 300;
// Auto-record delay: hold cutout visible for ~2 seconds before recording starts
const AUTO_RECORD_DELAY_MS = 2000;

/** Set to true to render translucent debug boxes for the upper/lower
 *  hit regions. See SubjectLiftLayer. */
export const DEBUG_SUBJECT_LIFT_OVERLAY = false;

/** Voice hotspot lives in the upper-right ~22%×18% box. We use it to
 *  skip SubjectLift so the voice handler can pick the press up. */
function isInVoiceHotspot(clientX: number, clientY: number) {
  const w = window.innerWidth;
  const h = window.innerHeight;
  return (
    clientX >= w * 0.72 && clientX <= w * 0.94 &&
    clientY >= h * 0.12 && clientY <= h * 0.30
  );
}

export type LiftState =
  | { status: "idle" }
  | { status: "pressing"; clientX: number; clientY: number; region: SubjectRegion }
  | { status: "extracting"; clientX: number; clientY: number; region: SubjectRegion }
  | {
      status: "armed_lifted";
      cutoutUrl: string;
      bbox: { x: number; y: number; width: number; height: number };
      region: SubjectRegion;
    }
  | {
      status: "starting";
      cutoutUrl: string;
      bbox: { x: number; y: number; width: number; height: number };
      region: SubjectRegion;
    }
  | {
      status: "recording";
      cutoutUrl: string;
      bbox: { x: number; y: number; width: number; height: number };
      region: SubjectRegion;
    }
  | {
      status: "releasing";
      cutoutUrl: string;
      bbox: { x: number; y: number; width: number; height: number };
      region: SubjectRegion;
    }
  | { status: "error"; message: string };

export type SubjectLiftHandlers = {
  onPointerDown: (e: React.PointerEvent<HTMLElement>) => void;
  onPointerMove: (e: React.PointerEvent<HTMLElement>) => void;
  onPointerUp: (e: React.PointerEvent<HTMLElement>) => void;
  onPointerCancel: (e: React.PointerEvent<HTMLElement>) => void;
  onContextMenu: (e: React.MouseEvent<HTMLElement>) => void;
};

export type UseSubjectLiftOptions = {
  imageSize: { width: number; height: number } | null;
  focusMode: "balanced" | "elder" | "child";
};

export function useSubjectLift(opts: UseSubjectLiftOptions) {
  const { imageSize, focusMode } = opts;

  const [state, setState] = useState<LiftState>({ status: "idle" });

  const uiMode = useSceneStore((s) => s.uiMode);
  const currentDayIndex = useSceneStore((s) => s.currentDayIndex);
  const currentWallpaperIndex = useSceneStore((s) => s.currentWallpaperIndex);
  const wallpapersByDay = useSceneStore((s) => s.wallpapersByDay);
  const initialInsertStatus = useSceneStore((s) => s.initialInsertStatus);
  const activeWallpaperUrl = useSceneStore(
    (state) => getSelectedWallpaper(state)?.imageUrl || "",
  );
  const latestWallpaperSelected = useSceneStore(isLatestWallpaper);
  // Reactive subscriptions for prefetch trigger.
  // URL is the primary identity — prefetch re-triggers when the wallpaper URL changes.
  const generatedWallpaperUrl = useSceneStore((s) => s.generatedWallpaperUrl);

  const role = useOnboardingStore((s) => s.role);
  const sessionViewerRole = useOnboardingStore(
    (s) => s.userContext?.viewerRole ?? null,
  );
  // The session-backed user context is authoritative. `role` is still used
  // during onboarding, but it can be stale or changed locally and must not
  // decide which family member may be lifted.
  const viewerRole: ViewerRole =
    sessionViewerRole === "elder" || sessionViewerRole === "child"
      ? sessionViewerRole
      : role === "elder" || role === "child"
        ? role
        : "elder";
  const relationshipId = useOnboardingStore(
    (s) => s.userContext?.relationshipId ?? "",
  );

  const recorder = useWallpaperVoiceEditRecorder();
  const {
    status: voiceStatus,
    subscribeGenerationSuccess,
    captureBusy,
    getCurrentCaptureId,
  } = recorder;

  // Subscribe to generation success events for historical auto-jump.
  // The event carries the full immutable context (captureId, relationshipId,
  // isFromHistoricalPage). When isFromHistoricalPage=true and eventSeq>0,
  // set expectedJumpEventSeq. No single historicalVoicePendingRef is needed —
  // each event carries its own attribution.
  useEffect(() => {
    const unsubscribe = subscribeGenerationSuccess(
      ({ eventSeq, captureId: _captureId, relationshipId, isFromHistoricalPage }) => {
        console.log(
          "[SubjectLift] generation success event",
          { eventSeq, relationshipId, isFromHistoricalPage },
        );
        if (isFromHistoricalPage && eventSeq > 0) {
          console.log(
            "[SubjectLift] historical page success, setting expected eventSeq",
            { eventSeq, relationshipId },
          );
          useSceneStore.getState().setExpectedJumpEventSeq(eventSeq, relationshipId);
        }
      },
    );
    return unsubscribe;
  }, [subscribeGenerationSuccess]);

  // When a new microphone capture begins, clear any pending jump expectations.
  // The next generation (if from a historical page) will set a fresh expectation.
  useEffect(() => {
    if (captureBusy) {
      console.log("[SubjectLift] capture starting, clearing any pending jump");
      useSceneStore.getState().setExpectedJumpEventSeq(null, "");
    }
  }, [captureBusy]);

  // ── Refs ──────────────────────────────────────────────────────────────
  const pressSessionIdRef = useRef(0);
  const recognizedSessionIdRef = useRef<number | null>(null);
  const extractionInFlightRef = useRef(false);
  const extractionAbortControllerRef = useRef<AbortController | null>(null);
  const extractionResultRef = useRef<{
    cutoutUrl: string;
    bbox: { x: number; y: number; width: number; height: number };
    region: SubjectRegion;
  } | null>(null);
  // Frozen interaction source: captured at pointerdown to prevent reactive store changes
  // from affecting an in-progress interaction.
  const interactionSourceRef = useRef<{
    sessionId: number;
    wallpaperUrl: string;
    revisionId: string;
    relationshipId: string;
    viewerRole: ViewerRole;
    personRole: PersonRole;
    region: SubjectRegion;
    // True when the interaction started from a historical (non-latest) page.
    // Used to trigger auto-jump to latest after generation completes.
    isFromHistoricalPage: boolean;
  } | null>(null);
  const autoRecordTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoRecordRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Counts how many transient retries have been attempted for the current armed session.
  // Reset whenever a new armed session starts (enterArmedLifted).
  const autoRecordRetryCountRef = useRef(0);
  const armedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Guards the start-recording click from being immediately interpreted as
  // a stop-recording trigger by the global recording-stop listener.
  const recordingStartedAtRef = useRef(0);
  // Flag set when user releases pointer during "starting" state.
  // When the async recorder start resolves, this is checked and triggers immediate stop.
  const pointerReleasedDuringStartRef = useRef(false);
  // Flag set when recorder truly starts, indicating a historical voice interaction is pending.
  // Consumed by generation success listener to set expectedJumpEventSeq.
  // Reset on pointer release during starting, recorder start failure, or
  // generation success. Stores the frozen relationshipId from the
  // interaction that started this generation AND the captureId that
  // emitted the success event, so overlap between captures can't poison
  // each other's pending state.
  const historicalVoicePendingRef = useRef<{
    relationshipId: string;
    captureId: string;
  } | null>(null);
  // External recorder stop request (from "click anywhere while recording").
  // The hook receives the imperative recorder handle from the layer so the
  // stop request is funneled through here.
  const onStopRecordingRef = useRef<(() => void) | null>(null);
  // Recorder start contract: must return a Promise that resolves to true if
  // the underlying recorder pipeline (microphone + WebSocket + AudioContext)
  // actually became ready, false on any failure. Returning true synchronously
  // is no longer supported — callers must await the real result.
  const onStartRecordingRef = useRef<
    ((viewerRole: ViewerRole) => Promise<boolean>) | null
  >(null);
  // Capture-finished callback fired by the recorder hook when the real
  // MediaRecorder / streaming pipeline actually stopped capture — NOT when
  // ASR / image generation finished. This is the authoritative signal that
  // drives Subject Lift's "recording -> releasing" transition, independent
  // of the voiceStatus store which spans the entire ASR + generation chain.
  const onCaptureFinishedRef = useRef<
    ((reason: "user_stop" | "silence" | "no_speech" | "error" | "max") => void) | null
  >(null);
  // Ref holding the recorder.start function. Set via registerRecorderBridge
  // so that doStartRecording can call recorder.start(ctx) directly with a frozen
  // VoiceProcessingContext, without going through the bridge callback.
  const recorderStartRef = useRef<((ctx: VoiceProcessingContext) => Promise<boolean>) | null>(
    null,
  );
  // Tracks whether the lift ever reached "recording" so we can tell real
  // recorder stop events apart from startup-phase idle -> recording flips.
  // This ref is mirrored into a tick-based state at the bottom of the hook
  // so React effects in the layer can subscribe to its changes.
  const hasEnteredRecordingRef = useRef(false);
  // Tracks whether the user is actively holding the pointer down.
  // Set to true on pointerdown, false on pointerup/pointercancel.
  // Used to block abnormal release triggers (armed timeout, silence, etc.)
  // when the user is still holding.
  const pointerHeldRef = useRef(false);

  // ── Synchronous state mirror ─────────────────────────────────────────
  // Keeps a ref in sync with the React state so async callbacks (cache hits,
  // in-flight joins) always read the current status without stale closures.
  const liftStateRef = useRef<LiftState>({ status: "idle" });
  const [, forceUpdate] = useState(0);

  // Call this helper for every state transition so the ref stays current.
  function transitionState(next: LiftState) {
    liftStateRef.current = next;
    setState(next);
  }

  // ── Gating conditions ───────────────────────────────────────────────
  // Allow Subject Lift on ANY wallpaper revision (historical or latest).
  // The cutout comes from the currently visible wallpaper; the voice
  // generation always uses the latest shared wallpaper on the backend.
  const canLiftChecks = {
    uiModeIsWallpaper: uiMode === "wallpaper",
    insertReady: initialInsertStatus === "ready",
    // Subject Lift must not run while a real microphone capture is live,
    // but a background ASR / image-generation pipeline from a previous
    // capture is allowed to keep running. The recorder hook exposes
    // captureBusy as a reactive state for this exact purpose.
    captureIdle: !captureBusy,
    hasGeneratedWallpaperUrl: !!activeWallpaperUrl,
    urlIsFinal:
      !!activeWallpaperUrl && activeWallpaperUrl.includes("/generated/"),
    focusBalanced: focusMode === "balanced",
    imageSizeReady: imageSize !== null,
  };
  const canLift = Object.values(canLiftChecks).every(Boolean);

  // Debug log for canLift changes
  const prevCanLiftRef = useRef(false);
  if (canLift !== prevCanLiftRef.current) {
    console.log("[subject_lift_debug]", {
      viewerRole,
      status: state.status,
      canLift,
      canLiftChecks,
      activeWallpaperUrl: activeWallpaperUrl ? activeWallpaperUrl.substring(0, 50) + "..." : null,
      imageSize,
      cutoutUrl: extractionResultRef.current?.cutoutUrl
        ? extractionResultRef.current.cutoutUrl.substring(0, 50) + "..."
        : null,
      bbox: extractionResultRef.current?.bbox || null,
      containerRect: "N/A",
      naturalWidth: imageSize?.width || null,
      naturalHeight: imageSize?.height || null,
      note: canLift ? "canLift changed to TRUE" : "canLift changed to FALSE",
    });
    prevCanLiftRef.current = canLift;
  }

  // ── Silent prefetch ───────────────────────────────────────────────────
  // After the shared wallpaper is fully synced, warm the cutout cache for the
  // current viewer so the first long-press hits the cache instead of the network.
  // Prefetches the currently visible wallpaper (not just latest) for faster response.
  useEffect(() => {
    // Gate: only prefetch when the subject-lift flow is available.
    if (!canLift) return;

    // Must have relationship context.
    if (!relationshipId) return;

    // Must have a visible wallpaper.
    if (!activeWallpaperUrl) return;

    // Determine the partner region for this viewer.
    const region = getPartnerRegion(viewerRole);
    const personRole = getPartnerRole(viewerRole);
    const anchor = getRegionAnchor(region);
    const selectedWallpaper = getSelectedWallpaper(useSceneStore.getState());
    const revisionId = selectedWallpaper?.revisionId ?? "";

    const key = buildSubjectCutoutCacheKey({
      relationshipId,
      revisionId,
      wallpaperUrl: activeWallpaperUrl,
      viewerRole,
      personRole,
      region,
    });

    // Skip if already cached or already in-flight.
    if (subjectCutoutCache.has(key)) return;
    if (subjectCutoutInFlight.has(key)) return;

    // Fire and forget — we don't await or store the promise in state.
    // Catches StaleSubjectCutoutError to prevent unhandled promise rejection.
    getOrRequestPartnerCutout({
      relationshipId,
      revisionId,
      wallpaperUrl: activeWallpaperUrl,
      viewerRole,
      personRole,
      region,
      normalizedX: anchor.normalizedX,
      normalizedY: anchor.normalizedY,
    }).catch((err) => {
      if (err instanceof StaleSubjectCutoutError) {
        // Expected: wallpaper changed while the prefetch was in flight.
        // Silently ignore — the next long-press will re-trigger.
        return;
      }
      console.warn("[subject_prefetch] extraction failed", {
        error: err instanceof Error ? err.message : String(err),
        revisionId,
      });
    });
  }, [
    // Re-trigger when the wallpaper identity changes.
    // activeWallpaperUrl covers revision-level changes (each revision has a distinct URL).
    // revisionId is included as a safety net for any future URL-stable revisions.
    activeWallpaperUrl,
    relationshipId,
    viewerRole,
    // canLift aggregates everything else; re-run when it becomes true.
    canLift,
  ]);

  // ── Auto-jump to latest after historical page generation ──────────────
  // When a historical voice interaction succeeds, the backend sets expectedJumpEventSeq
  // via setExpectedJumpEventSeq. When the revision appears in wallpapersByDay with
  // matching eventSeq AND relationshipId, we jump to latest. Exact match prevents
  // partner updates from triggering false jumps. RelationshipId binding prevents
  // cross-relationship pollution.
  const setCurrentDayIndex = useSceneStore((s) => s.setCurrentDayIndex);
  const expectedJumpEventSeq = useSceneStore((s) => s.expectedJumpEventSeq);
  const expectedJumpRelationshipId = useSceneStore((s) => s.expectedJumpRelationshipId);

  useEffect(() => {
    if (!expectedJumpEventSeq) return;
    // Guard against relationship change: only proceed if current relationship matches
    const currentRelationshipId = relationshipId;
    if (expectedJumpRelationshipId && expectedJumpRelationshipId !== currentRelationshipId) {
      console.log("[SubjectLift] relationship changed, clearing stale pending jump", {
        expectedRelId: expectedJumpRelationshipId,
        currentRelId: currentRelationshipId,
      });
      useSceneStore.getState().setExpectedJumpEventSeq(null, "");
      return;
    }

    // Defer auto-jump while the user is mid-Lift on a different
    // revision. Showing a new wallpaper while a cutout from a historical
    // revision is still lifted would visually overlay two revisions and
    // break the pointer-capture experience.
    const liftStatus = liftStateRef.current.status;
    if (
      liftStatus === "pressing" ||
      liftStatus === "extracting" ||
      liftStatus === "armed_lifted" ||
      liftStatus === "starting" ||
      liftStatus === "recording" ||
      liftStatus === "releasing"
    ) {
      console.log(
        "[SubjectLift] deferring auto-jump, lift state active",
        { liftStatus, expectedJumpEventSeq },
      );
      return;
    }

    const currentUrl = generatedWallpaperUrl;
    if (!currentUrl) return;

    // Check if our expected revision has arrived in today's wallpapers
    const todayWallpapers = wallpapersByDay[DAY_LABELS[TODAY_INDEX]] ?? [];
    const matchingRevision = todayWallpapers.find(
      (w: WallpaperItem) => w.eventSeq === expectedJumpEventSeq,
    );

    if (matchingRevision) {
      console.log("[SubjectLift] expected revision arrived, auto-jumping to latest", {
        expectedEventSeq: expectedJumpEventSeq,
      });
      setCurrentDayIndex(TODAY_INDEX);
      // Clear expected eventSeq after jump
      useSceneStore.getState().setExpectedJumpEventSeq(null, "");
    }
  }, [generatedWallpaperUrl, wallpapersByDay, expectedJumpEventSeq, expectedJumpRelationshipId, relationshipId, setCurrentDayIndex]);

  // ── Cleanup helpers ─────────────────────────────────────────────────
  const clearAutoRecordTimer = useCallback(() => {
    if (autoRecordTimerRef.current !== null) {
      clearTimeout(autoRecordTimerRef.current);
      autoRecordTimerRef.current = null;
    }
  }, []);

  const clearAutoRecordRetryTimer = useCallback(() => {
    if (autoRecordRetryTimerRef.current !== null) {
      clearTimeout(autoRecordRetryTimerRef.current);
      autoRecordRetryTimerRef.current = null;
    }
  }, []);

  const cancelPress = useCallback(() => {
    pressSessionIdRef.current = 0;
    recognizedSessionIdRef.current = null;
    clearAutoRecordTimer();
    clearAutoRecordRetryTimer();
    // NOTE: pointerHeldRef is NOT cleared here. It is owned exclusively by
    // real browser pointer events (onPointerDown → true, onPointerUp/onPointerCancel → false).
    // cancelPress() resets stale session state only — it does NOT represent a real
    // pointer release, so it must not override the pointer ownership flag.
    // Note: in-flight module-level requests are NOT aborted here.
    // They complete and are guarded by the stale check.
  }, [clearAutoRecordTimer, clearAutoRecordRetryTimer]);

  const clearArmedTimeout = useCallback(() => {
    if (armedTimeoutRef.current !== null) {
      clearTimeout(armedTimeoutRef.current);
      armedTimeoutRef.current = null;
    }
  }, []);

  const enterReleasing = useCallback((
    cutoutUrl: string,
    bbox: { x: number; y: number; width: number; height: number },
    region: SubjectRegion,
  ) => {
    clearArmedTimeout();
    clearAutoRecordTimer();
    clearAutoRecordRetryTimer();
    transitionState({ status: "releasing", cutoutUrl, bbox, region });
  }, [clearArmedTimeout, clearAutoRecordTimer, clearAutoRecordRetryTimer]);

  // ── Start recording ──────────────────────────────────────────────────
  // Clears the 10-second armed timeout, transitions through "starting" while
  // awaiting the real recorder pipeline, and only enters "recording" once
  // the pipeline is verified live. If the pipeline fails, the state falls
  // back to "armed_lifted" so the subject visual stays lifted and the user
  // can retry — never a phantom recorder.
  const startRecording = useCallback(
    async (viewerRole: ViewerRole): Promise<boolean> => {
      if (liftStateRef.current.status !== "armed_lifted") {
        console.log(
          "[SubjectLift] startRecording blocked: not armed_lifted",
          liftStateRef.current.status,
        );
        return false;
      }

      // Snapshot current visual state for the await.
      const result = extractionResultRef.current;
      if (!result) {
        console.log("[SubjectLift] startRecording blocked: no extraction result");
        return false;
      }

      // Clear the armed timeout — recording is now in control.
      clearArmedTimeout();
      recordingStartedAtRef.current = Date.now();

      console.log("[SUBJECT_LIFT] starting", {
        region: result.region,
        viewerRole,
      });
      console.log("[subject_lift_transition]", {
        from: "armed_lifted",
        to: "starting",
        reason: "auto_record_timer",
        sessionId: pressSessionIdRef.current,
      });
      transitionState({ status: "starting", ...result });

      // Await real recorder readiness via the bridge-installed recorderStart.
      const hook = recorderStartRef.current;
      if (!hook) {
        console.warn(
          "[SubjectLift] startRecording aborted: no recorder bridge registered",
        );
        // Roll back to armed_lifted so the user can retry.
        transitionState({ status: "armed_lifted", ...result });
        return false;
      }

      // Build frozen context once. The hook will set activeContextRef immediately.
      const source = interactionSourceRef.current;
      const relationshipId = useOnboardingStore.getState().userContext?.relationshipId ?? "";
      const ctx: VoiceProcessingContext = {
        captureId: crypto.randomUUID(),
        relationshipId,
        isFromHistoricalPage: source?.isFromHistoricalPage ?? false,
        source: "subject_lift",
      };

      let ok = false;
      let okReason = "unknown";
      try {
        ok = await hook(ctx);
        okReason = ok ? "ok" : "bridge_returned_false";
      } catch (err) {
        console.error("[SubjectLift] recorder bridge threw", err);
        ok = false;
        okReason = err instanceof Error ? `throw:${err.message}` : "throw:unknown";
      }

      console.log("[SubjectLift] auto-record start result", {
        ok,
        reason: okReason,
        currentState: liftStateRef.current.status,
        currentRevisionId: extractionResultRef.current
          ? undefined
          : undefined,
        pointerReleasedDuringStart: pointerReleasedDuringStartRef.current,
      });

      // If user released pointer during "starting", stop immediately.
      // We check the flag (set by pointerup) to handle the race condition.
      // DO NOT transition UI state - pointerup already entered releasing.
      if (pointerReleasedDuringStartRef.current) {
        console.log("[SubjectLift] pointer released during starting, stopping recorder");
        pointerReleasedDuringStartRef.current = false;
        // Clear historical voice pending since no valid recording will happen.
        historicalVoicePendingRef.current = null;
        const stopHook = onStopRecordingRef.current;
        if (stopHook && ok) {
          stopHook();
        }
        // Just do cleanup, don't transition UI (pointerup already handled it)
        return false;
      }

      if (!ok) {
        console.log(
          "[SubjectLift] auto-record start failed",
          { viewerRole, reason: okReason, pointerHeld: pointerHeldRef.current },
        );
        // Clear historical voice pending flag since no generation will happen.
        historicalVoicePendingRef.current = null;

        // Fatal failure: the recorder can never start (permission denied, no microphone,
        // unsupported browser). These are not retriable. If the user is still holding,
        // stay lifted and wait for real pointerUp — do NOT auto-release.
        const isFatal =
          okReason.includes("permission") ||
          okReason.includes("microphone") ||
          okReason.includes("not allowed") ||
          okReason.includes("not_found") ||
          okReason.includes("no supported") ||
          okReason.includes("unsupported");
        if (isFatal && pointerHeldRef.current) {
          console.warn(
            "[SubjectLift] recorder fatal start failure — waiting for pointerUp",
            { reason: okReason },
          );
          // Stay in armed_lifted; do NOT enterReleasing().
          if (extractionResultRef.current) {
            transitionState({ status: "armed_lifted", ...extractionResultRef.current });
          }
          return false;
        }

        // In hold-to-record mode, if the user is still holding the pointer,
        // the first failure gets one automatic retry after a short delay.
        // This handles transient states like capture_busy during a prior cleanup.
        if (pointerHeldRef.current && autoRecordRetryCountRef.current < AUTO_RECORD_MAX_RETRIES) {
          autoRecordRetryCountRef.current += 1;
          console.log(
            "[SubjectLift] scheduling transient retry",
            {
              attempt: autoRecordRetryCountRef.current,
              maxRetries: AUTO_RECORD_MAX_RETRIES,
              retryDelayMs: AUTO_RECORD_RETRY_DELAY_MS,
            },
          );
          // Stay in armed_lifted while waiting for retry
          if (extractionResultRef.current) {
            transitionState({ status: "armed_lifted", ...extractionResultRef.current });
          }
          autoRecordRetryTimerRef.current = setTimeout(() => {
            // Guard: only retry if still in armed_lifted and pointer still held
            if (liftStateRef.current.status !== "armed_lifted") {
              console.log("[SubjectLift] retry skipped: no longer armed_lifted");
              return;
            }
            if (!pointerHeldRef.current) {
              console.log("[SubjectLift] retry skipped: pointer already released");
              return;
            }
            console.log("[SubjectLift] transient retry fired, retrying recorder start");
            void doStartRecording();
          }, AUTO_RECORD_RETRY_DELAY_MS);
          return false;
        }

        // Retry exhausted — stay in armed_lifted and wait for real pointerUp.
        // The user is still holding, so we must NOT force a visual release.
        // Only enterReleasing when the user actually lets go.
        autoRecordRetryCountRef.current = 0;
        console.log(
          "[SubjectLift] auto-record retry exhausted — waiting for pointerUp",
          { viewerRole, reason: okReason },
        );
        // Stay in armed_lifted. Do NOT call enterReleasing().
        // The pointerUp handler will handle the release when the user lets go.
        if (extractionResultRef.current) {
          transitionState({ status: "armed_lifted", ...extractionResultRef.current });
        }
        return false;
      }

      hasEnteredRecordingRef.current = true;
      setHasEnteredRecording(true);
      // Pin a new recording-session sequence id so any stale
      // capture-finished notification from a previous (now superseded)
      // recording can be ignored.
      currentRecordingSeqRef.current = ++startRecordingSeqRef.current;
      // Set historical voice pending flag if this is from a historical page.
      // This flag will be consumed when generation succeeds or fails.
      // Store the frozen relationshipId + the recorder-side captureId so
      // overlap captures cannot poison each other's pending context.
      if (interactionSourceRef.current?.isFromHistoricalPage === true) {
        historicalVoicePendingRef.current = {
          relationshipId: interactionSourceRef.current.relationshipId ?? "",
          captureId: getCurrentCaptureId() ?? "",
        };
      } else {
        historicalVoicePendingRef.current = null;
      }
      console.log("[SUBJECT_LIFT] recording", {
        region: result.region,
        viewerRole,
        seq: currentRecordingSeqRef.current,
      });
      console.log("[subject_lift_transition]", {
        from: "starting",
        to: "recording",
        reason: "recorder_pipeline_ready",
        sessionId: pressSessionIdRef.current,
      });
      transitionState({ status: "recording", ...result });
      return true;
    },
    [clearArmedTimeout],
  );

  // ── Internal helper to start recording ─────────────────────────────────
  // Calls startRecording with current viewerRole. Used by auto-record timer.
  const doStartRecording = useCallback(async () => {
    if (liftStateRef.current.status !== "armed_lifted") return;
    const source = interactionSourceRef.current;
    const relationshipId = useOnboardingStore.getState().userContext?.relationshipId ?? "";
    const ctx: VoiceProcessingContext = {
      captureId: crypto.randomUUID(),
      relationshipId,
      isFromHistoricalPage: source?.isFromHistoricalPage ?? false,
      source: "subject_lift",
    };
    const hook = recorderStartRef.current;
    if (hook) {
      await hook(ctx);
    }
  }, []);

  // ── Enter armed_lifted and start auto-record timer ────────────────────
  // After extraction completes, immediately enter armed_lifted and start
  // the auto-record timer. If user releases before timer fires, cancel.
  const enterArmedLifted = useCallback(() => {
    const result = extractionResultRef.current;
    if (!result) {
      console.log("[SubjectLift] enterArmedLifted: no extraction result");
      return;
    }

    console.log("[SubjectLift] entering armed_lifted", {
      cutoutUrl: result.cutoutUrl.substring(0, 50) + "...",
      bbox: result.bbox,
      region: result.region,
    });

    clearAutoRecordTimer();
    clearArmedTimeout();
    // Clear any pending retry timer from a previous armed session.
    if (autoRecordRetryTimerRef.current !== null) {
      clearTimeout(autoRecordRetryTimerRef.current);
      autoRecordRetryTimerRef.current = null;
    }
    // Reset retry count for the new armed session.
    autoRecordRetryCountRef.current = 0;

    // Start a "safety" timeout — this only fires if the user somehow got
    // stuck in armed_lifted WITHOUT pointerHeld (e.g. pointercancel fired without
    // calling onPointerCancel). In normal hold-to-record flow, the pointer is
    // always held until pointerup, so this should never fire.
    armedTimeoutRef.current = setTimeout(() => {
      if (pointerHeldRef.current) {
        // Pointer is still held — this is a bug in pointer tracking.
        // Don't release; log a warning instead.
        console.warn(
          "[SubjectLift] ARMED_TIMEOUT fired but pointer is still held — ignoring",
        );
        return;
      }
      console.log("[subject_lift_release] reason=armed_timeout (pointer released)");
      if (extractionResultRef.current) {
        enterReleasing(
          extractionResultRef.current.cutoutUrl,
          extractionResultRef.current.bbox,
          extractionResultRef.current.region,
        );
      }
    }, ARMED_TIMEOUT_MS);

    // Start auto-record timer: recording starts ~2 seconds after cutout is visible
    autoRecordTimerRef.current = setTimeout(() => {
      console.log("[SubjectLift] auto-record timer fired, starting recording");
      // Check if still in armed_lifted state
      if (liftStateRef.current.status !== "armed_lifted") {
        console.log("[SubjectLift] auto-record ignored: no longer armed_lifted");
        return;
      }
      void doStartRecording();
    }, AUTO_RECORD_DELAY_MS);

    console.log("[subject_lift_armed]", {
      armed_timeout_ms: ARMED_TIMEOUT_MS,  // safety timeout: only fires if pointerReleased without proper cleanup
      auto_record_delay_ms: AUTO_RECORD_DELAY_MS,
      region: result.region,
      personRole: getPersonRoleByRegion(result.region),
    });

    transitionState({ status: "armed_lifted", ...result });
  }, [clearArmedTimeout, clearAutoRecordTimer, enterReleasing, doStartRecording]);

  // ── Reset when lift feature becomes ineligible ────────────────────────
  // Only collapse an *idle-ish* lift state when canLift flips off. Crucially
  // we MUST NOT yank the state back to "idle" while we are inside
  // "starting" or "recording" — those states own the user's active session,
  // and the voiceStatus store flips to "recording"/"transcribing"/"editing"
  // for the entire ASR + image-generation chain, which would otherwise
  // visually kill the lifted subject mid-recording.
  useEffect(() => {
    if (canLift) return;
    const current = liftStateRef.current.status;
    if (
      current === "starting" ||
      current === "recording" ||
      current === "releasing"
    ) {
      // Active recording session: leave state alone. The
      // notifyCaptureFinished bridge is the authoritative release signal.
      return;
    }
    cancelPress();
    clearArmedTimeout();
    clearAutoRecordTimer();
    extractionResultRef.current = null;
    setHasEnteredRecording(false);
    transitionState({ status: "idle" });
  }, [canLift, cancelPress, clearArmedTimeout, clearAutoRecordTimer]);

  // ── Releasing timeout ────────────────────────────────────────────────
  useEffect(() => {
    if (state.status !== "releasing") return;
    const id = window.setTimeout(() => {
      extractionResultRef.current = null;
      setHasEnteredRecording(false);
      transitionState({ status: "idle" });
    }, RELEASING_DURATION_MS);
    return () => window.clearTimeout(id);
  }, [state]);

  // ── Extraction logic ──────────────────────────────────────────────────
  const runExtraction = useCallback(async (
    clientX: number,
    clientY: number,
    region: SubjectRegion,
    sessionId: number,
  ) => {
    if (!imageSize) {
      console.log("[SubjectLift] blocked: imageSize not ready");
      transitionState({ status: "idle" });
      return;
    }

    const container = document.querySelector<HTMLElement>(
      "[data-subject-lift-container]",
    );
    if (!container) {
      console.warn("[SubjectLift] no container found");
      transitionState({ status: "error", message: "no container" });
      return;
    }

    const display = getWallpaperDisplayGeometry();
    const rect = display?.rect ?? container.getBoundingClientRect();
    const pt = clientPointToImagePoint({
      clientX,
      clientY,
      imageRect: rect,
      naturalWidth: imageSize.width,
      naturalHeight: imageSize.height,
      objectFit: "cover",
      objectPositionX: display?.objectPositionX,
    });

    if (!pt.inside) {
      console.log("[SubjectLift] pointer outside image, ignored");
      transitionState({ status: "idle" });
      return;
    }

    recognizedSessionIdRef.current = sessionId;
    transitionState({ status: "extracting", clientX, clientY, region });

    // Use frozen interaction source
    const source = interactionSourceRef.current;
    if (!source || source.sessionId !== sessionId) {
      console.log("[SubjectLift] interaction source not available or session mismatch");
      transitionState({ status: "idle" });
      return;
    }

    // ── Diagnostic log: every real long-press attempt ────────────────────
    const selectedWallpaper = getSelectedWallpaper(useSceneStore.getState());
    const dayIndex = useSceneStore.getState().currentDayIndex;
    const wallpaperIndex = useSceneStore.getState().currentWallpaperIndex;
    console.log("[SUBJECT_LIFT_ATTEMPT]", {
      revisionId: source.revisionId,
      wallpaperUrl: source.wallpaperUrl?.substring(0, 60),
      dayIndex,
      wallpaperIndex,
      canLift,
      canLiftChecks,
      region: source.region,
    });

    console.log("[SUBJECT_EXTRACT_REQUEST]", {
      revisionId: source.revisionId,
      relationshipId: source.relationshipId,
      region: source.region,
      viewerRole: source.viewerRole,
    });

    let result: SubjectExtractionResult | null = null;

    try {
      // Use the shared helper: cache hit → immediate; in-flight join → no dup;
      // live → POST.  We use the frozen interaction source, not reactive values.
      result = await getOrRequestPartnerCutout({
        relationshipId: source.relationshipId,
        revisionId: source.revisionId,
        wallpaperUrl: source.wallpaperUrl,
        viewerRole: source.viewerRole,
        personRole: source.personRole,
        region: source.region,
        normalizedX: pt.normalizedX,
        normalizedY: pt.normalizedY,
      });
    } catch (err) {
      if (err instanceof StaleSubjectCutoutError) {
        // The wallpaper changed while the request was in flight.
        // Only go to idle if this response is still for the current session.
        // An old async callback should NOT reset a newer active session.
        if (recognizedSessionIdRef.current !== sessionId) {
          console.log("[SubjectLift] stale cutout result discarded (old callback, new session active)", {
            sessionId,
            recognizedSessionId: recognizedSessionIdRef.current,
            reasons: err.reasons,
          });
          return;
        }
        console.log("[SubjectLift] stale cutout result discarded", {
          sessionId,
          reasons: err.reasons,
        });
        transitionState({ status: "idle" });
        return;
      }

      if (recognizedSessionIdRef.current !== sessionId) {
        console.log("[subject_lift_stale_response_ignored]", {
          site: "catch",
          sessionId,
          recognizedSessionId: recognizedSessionIdRef.current,
          reason: "session_reset_or_new_session",
        });
        return;
      }
      if (
        err instanceof Error &&
        (err.name === "AbortError" || err.message?.includes("abort"))
      ) {
        console.log("[SubjectLift] extraction aborted");
      } else {
        console.warn("[SubjectLift] extract failed:", err);
        transitionState({
          status: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      }
      transitionState({ status: "idle" });
      return;
    }

    // Check stale session
    if (recognizedSessionIdRef.current !== sessionId) {
      console.log("[subject_lift_stale_response_ignored]", {
        site: "post_await",
        sessionId,
        recognizedSessionId: recognizedSessionIdRef.current,
        reason: "session_reset_or_new_session",
      });
      return;
    }

    if (!result) {
      console.warn("[SubjectLift] extract returned null");
      transitionState({ status: "idle" });
      return;
    }

    // Determine source for logging
    const sourceKey = interactionSourceRef.current
      ? buildSubjectCutoutCacheKey({
          relationshipId: interactionSourceRef.current.relationshipId,
          revisionId: interactionSourceRef.current.revisionId,
          wallpaperUrl: interactionSourceRef.current.wallpaperUrl,
          viewerRole: interactionSourceRef.current.viewerRole,
          personRole: interactionSourceRef.current.personRole,
          region: interactionSourceRef.current.region,
        })
      : null;
    const wasCached = sourceKey ? subjectCutoutCache.has(sourceKey) : false;

    console.log("[SUBJECT_EXTRACT_RESPONSE]", {
      revisionId: source.revisionId,
      region: source.region,
      success: true,
      cached: wasCached,
      cutoutUrl: result.cutoutUrl.substring(0, 50) + "...",
      bbox: result.bbox,
    });

    // Validate the press session is still active before accepting this result.
    // Only accept in "extracting" state for hold-to-record flow.
    const currentStatus = liftStateRef.current.status;
    const isStaleSession =
      recognizedSessionIdRef.current !== sessionId ||
      currentStatus !== "extracting";

    if (isStaleSession) {
      console.log("[subject_lift_stale_response_ignored]", {
        site: "session_validation",
        sessionId,
        recognizedSessionId: recognizedSessionIdRef.current,
        currentStatus,
        reason: "session_invalidated",
      });
      transitionState({ status: "idle" });
      return;
    }

    // Also re-verify the viewer can still operate on this region (role change mid-session guard).
    if (!isRegionLiftingAllowed(viewerRole, region)) {
      console.log("[subject_lift] region no longer allowed for viewer", {
        viewerRole,
        region,
      });
      transitionState({ status: "idle" });
      return;
    }

    // Store the extraction result
    extractionResultRef.current = {
      cutoutUrl: result.cutoutUrl,
      bbox: result.bbox,
      region,
    };

    console.log("[subject_lift_extraction_completed]", {
      sessionId,
      region,
      cutoutUrl: result.cutoutUrl.substring(0, 50) + "...",
      bbox: result.bbox,
    });

    // Immediately enter armed_lifted state and start auto-record timer
    console.log("[subject_lift_transition]", {
      from: "extracting",
      to: "armed_lifted",
      sessionId,
      region,
    });
    enterArmedLifted();
  }, [activeWallpaperUrl, imageSize, viewerRole, enterArmedLifted]);

  // ── Pointer down handler ─────────────────────────────────────────────
  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      console.log(
        "[SubjectLift] pointerdown",
        JSON.stringify({
          clientX: e.clientX,
          clientY: e.clientY,
          pointerType: e.pointerType,
          status: state.status,
        }),
      );

      if (!canLift) {
        console.log("[SubjectLift] blocked: canLift = false");
        return;
      }

      // Ignore secondary pointers
      if (!e.isPrimary) {
        console.log("[SubjectLift] ignored non-primary pointer");
        return;
      }

      // Mouse: only left button
      if (e.pointerType === "mouse" && e.button !== 0) {
        console.log("[SubjectLift] ignored non-left mouse button", e.button);
        return;
      }

      if (!imageSize) {
        console.log("[SubjectLift] blocked: imageSize not ready");
        return;
      }

      const container = document.querySelector<HTMLElement>(
        "[data-subject-lift-container]",
      );
      if (!container) {
        console.warn("[SubjectLift] no container found");
        return;
      }

      const display = getWallpaperDisplayGeometry();
      const rect = display?.rect ?? container.getBoundingClientRect();
      const pt = clientPointToImagePoint({
        clientX: e.clientX,
        clientY: e.clientY,
        imageRect: rect,
        naturalWidth: imageSize.width,
        naturalHeight: imageSize.height,
        objectFit: "cover",
        objectPositionX: display?.objectPositionX,
      });

      if (!pt.inside) {
        console.log("[SubjectLift] pointer outside image, ignored");
        return;
      }

      // Hit test to determine which region was pressed
      const candidateRegion = hitTestSubject(pt.normalizedX, pt.normalizedY);

      console.log("[SubjectLift] hitTest", {
        nx: pt.normalizedX,
        ny: pt.normalizedY,
        candidateRegion,
      });

      if (!candidateRegion) {
        console.log("[SubjectLift] pointerdown outside subject region, ignored");
        return;
      }

      // ── Permission check ────────────────────────────────────────────
      const personRole = getPersonRoleByRegion(candidateRegion);
      const partnerRole = getPartnerRole(viewerRole);
      const partnerRegion = getPartnerRegion(viewerRole);

      console.log("[subject_lift_permission]", {
        viewerRole,
        candidateRegion,
        personRole,
        partnerRole,
        partnerRegion,
        allowed: candidateRegion === partnerRegion,
      });

      if (!isRegionLiftingAllowed(viewerRole, candidateRegion)) {
        console.log("[SubjectLift] blocked: not partner region");
        return;
      }

      // Belt-and-braces: don't compete with the Voice hotspot
      const inVoice =
        latestWallpaperSelected && isInVoiceHotspot(e.clientX, e.clientY);
      if (inVoice) {
        console.log("[SubjectLift] pointerdown over voice hotspot, ignored");
        return;
      }

      // ── Reset prior session state first ─────────────────────────────────
      // This clears stale timers, session IDs, and extraction results from
      // the previous press. It does NOT modify pointerHeldRef — that flag
      // is owned exclusively by real pointer events.
      cancelPress();
      extractionResultRef.current = null;
      setHasEnteredRecording(false);

      // ── Then claim pointer ownership for the new press ──────────────────
      // pointerHeldRef must be set AFTER cancelPress so a new real touch
      // is never immediately clobbered by cancelPress clearing it.
      pointerHeldRef.current = true;
      // Always reset the pointer-during-start race flag so that a previous
      // session's stale "true" cannot silently abort the next startRecording.
      pointerReleasedDuringStartRef.current = false;
      const sessionId = ++pressSessionIdRef.current;

      // Freeze the interaction source at pointerdown time
      const relationshipId = useOnboardingStore.getState().userContext?.relationshipId ?? "";
      const selectedWallpaper = getSelectedWallpaper(useSceneStore.getState());
      interactionSourceRef.current = {
        sessionId,
        wallpaperUrl: activeWallpaperUrl,
        revisionId: selectedWallpaper?.revisionId ?? "",
        relationshipId,
        viewerRole,
        personRole: getPartnerRole(viewerRole),
        region: candidateRegion,
        isFromHistoricalPage: !latestWallpaperSelected,
      };

      transitionState({
        status: "pressing",
        clientX: e.clientX,
        clientY: e.clientY,
        region: candidateRegion,
      });

      console.log("[SubjectLift] timer started", {
        sessionId,
        region: candidateRegion,
        longPressMs: LONG_PRESS_MS,
      });

      setTimeout(() => {
        if (pressSessionIdRef.current !== sessionId) {
          console.log("[SubjectLift] timer fired but session expired");
          return;
        }
        if (recognizedSessionIdRef.current !== null) {
          console.log("[SubjectLift] timer fired but already recognized");
          return;
        }
        void runExtraction(e.clientX, e.clientY, candidateRegion, sessionId);
      }, LONG_PRESS_MS);
    },
    [canLift, imageSize, viewerRole, latestWallpaperSelected, cancelPress, runExtraction, state.status],
  );

  // ── Pointer move handler ────────────────────────────────────────────
  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (pressSessionIdRef.current === 0) return;

      const startState = state.status === "pressing" ? state : null;
      if (!startState) return;

      const dx = e.clientX - startState.clientX;
      const dy = e.clientY - startState.clientY;

      if (Math.hypot(dx, dy) > MOVE_TOLERANCE_PX) {
        console.log("[SubjectLift] pointermove exceeded tolerance, cancelling");
        cancelPress();
        transitionState({ status: "idle" });
      }
    },
    [state, cancelPress],
  );

  // ── Pointer up handler ───────────────────────────────────────────────
  // For hold-to-record flow:
  // - If in pressing/extracting: cancel the interaction
  // - If in armed_lifted: user released before recording started → cancel
  // - If in recording: user released → stop recording
  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      // User has released the pointer — clear held flag.
      pointerHeldRef.current = false;

      console.log(
        "[SubjectLift] pointerup",
        JSON.stringify({
          status: state.status,
          hasExtractionResult: !!extractionResultRef.current,
        }),
      );

      // If we were in pressing state, cancel
      if (state.status === "pressing") {
        cancelPress();
        transitionState({ status: "idle" });
        return;
      }

      // If we are in extracting state, cancel the extraction and interaction
      if (state.status === "extracting") {
        console.log("[SubjectLift] pointerup while extracting, cancelling");
        cancelPress();
        transitionState({ status: "idle" });
        return;
      }

  // If we are in armed_lifted state, user released before recording started
  // Cancel auto-record timer and enter releasing (no recording)
  if (state.status === "armed_lifted") {
    console.log("[SubjectLift] pointerup while armed_lifted, cancelling (no recording)");
    clearAutoRecordTimer();
    if (extractionResultRef.current) {
      enterReleasing(
        extractionResultRef.current.cutoutUrl,
        extractionResultRef.current.bbox,
        extractionResultRef.current.region,
      );
    } else {
      transitionState({ status: "idle" });
    }
    return;
  }

  // If we are in starting state, the user released while recorder was initializing.
  // Immediately enter releasing for visual feedback. The async recorder start will
  // still resolve, but we check pointerReleasedDuringStartRef to avoid entering recording.
  if (state.status === "starting") {
    console.log("[SubjectLift] pointerup while starting, immediately releasing");
    pointerReleasedDuringStartRef.current = true;
    // Immediate visual feedback: show releasing animation
    // In starting state, extractionResultRef.current is always populated
    if (extractionResultRef.current) {
      enterReleasing(
        extractionResultRef.current.cutoutUrl,
        extractionResultRef.current.bbox,
        extractionResultRef.current.region,
      );
    } else {
      // Fallback: shouldn't happen in starting state, but safe fallback
      transitionState({ status: "idle" });
    }
    return;
  }

  // If we are in releasing state, ignore
  if (state.status === "releasing") {
    return;
  }
    },
    [state, cancelPress, clearAutoRecordTimer, enterReleasing],
  );

  // ── Pointer cancel handler ────────────────────────────────────────────
  // Note: pointercancel is treated as a true cancellation — it aborts
  // any in-flight extraction and resets the session.
  const onPointerCancel = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      // Pointer was cancelled (e.g. touch interrupted) — clear held flag.
      pointerHeldRef.current = false;
      console.log("[SubjectLift] pointercancel", { status: state.status });

      const wasExtracting = state.status === "extracting";
      const sessionId = recognizedSessionIdRef.current;

      cancelPress();

      console.log("[subject_lift_pointer_cancelled]", {
        sessionId,
        priorStatus: state.status,
        wasExtracting,
      });

      if (state.status === "armed_lifted" && extractionResultRef.current) {
        clearAutoRecordTimer();
        enterReleasing(
          extractionResultRef.current.cutoutUrl,
          extractionResultRef.current.bbox,
          extractionResultRef.current.region,
        );
      } else {
        extractionResultRef.current = null;
        setHasEnteredRecording(false);
        transitionState({ status: "idle" });
      }
    },
    [state, cancelPress, clearAutoRecordTimer, enterReleasing],
  );

  const onContextMenu = useCallback((e: React.MouseEvent<HTMLElement>) => {
    e.preventDefault();
  }, []);

  // ── Cleanup on unmount ───────────────────────────────────────────────
  useEffect(() => {
    console.log("[SubjectLift] mounted");
    return () => {
      console.log("[SubjectLift] unmounted");
      cancelPress();
      clearArmedTimeout();
    };
  }, [cancelPress, clearArmedTimeout]);

  // ── Stop recording (called by layer on click-anywhere-while-recording) ──
  // Triggers the recorder stop (which will eventually flip status to
  // "transcribing/editing") and immediately enters the releasing animation.
  const stopRecording = useCallback(
    (
      trigger:
        | "user_stop"
        | "recorder_error"
        | "recorder_auto_stop"
        | "starting_cancelled" = "user_stop",
    ): boolean => {
      // Special path: stop while still "starting" means the user clicked
      // again before the recorder pipeline finished. Roll back to
      // armed_lifted without telling the recorder to stop (it never really
      // started, or it failed and already returned false upstream).
      if (state.status === "starting") {
        const result = extractionResultRef.current;
        if (!result) {
          transitionState({ status: "idle" });
          return false;
        }
        console.log(
          "[SubjectLift] stopRecording while starting: rolling back to armed_lifted",
          { trigger },
        );
        transitionState({ status: "armed_lifted", ...result });
        return true;
      }
      if (state.status !== "recording") {
        console.log(
          "[SubjectLift] stopRecording blocked: not recording",
          state.status,
        );
        return false;
      }
      const result = extractionResultRef.current;
      if (!result) {
        console.log("[SubjectLift] stopRecording blocked: no extraction result");
        return false;
      }

      // Guard against the SAME pointer session that started recording
      // immediately triggering stop. The 300ms window absorbs the synthetic
      // click that follows pointerdown/click on the lifted subject.
      const sinceStart = Date.now() - recordingStartedAtRef.current;
      if (sinceStart < REC_START_STOP_GUARD_MS) {
        console.log("[SubjectLift] stopRecording suppressed by start-guard", {
          sinceStart,
        });
        return false;
      }

      console.log("[SUBJECT_LIFT] releasing", { trigger });
      console.log("[subject_lift_transition]", {
        from: "recording",
        to: "releasing",
        reason: `stop_recording:${trigger}`,
        sessionId: pressSessionIdRef.current,
      });
      console.log(`[subject_lift_release] reason=${trigger} status=recording`);
      clearArmedTimeout();
      hasEnteredRecordingRef.current = false;
      setHasEnteredRecording(false);
      transitionState({ status: "releasing", ...result });
      // Tell the layer to actually stop the recorder (media recorder stop is
      // async; the recorder will transition to "transcribing/editing" later
      // but the visual fall-back must begin immediately).
      void onStopRecordingRef.current?.();
      return true;
    },
    [state.status, clearArmedTimeout],
  );

  // ── Capture-finished bridge ────────────────────────────────────────────
  // The recorder hook calls this the moment real audio capture stops
  // (MediaRecorder.onstop / streaming flush done / fallback to flash / error
  // path), independent of voiceStatus. This drives the authoritative
  // "recording -> releasing" transition so the Subject Lift visual never
  // lingers on the capture-pulse animation while ASR / image generation
  // continues for 30-60 seconds.
  //
  // Behaviour:
  //   - state.status === "recording" + user_stop: transition to "releasing"
  //   - state.status === "recording" + abnormal stop + pointer held: IGNORED
  //     (silence/no_speech/max/error don't end a recording the user wants to keep)
  //   - state.status === "recording" + abnormal stop + pointer released: transition to "releasing"
  //   - state.status === "starting":  roll back to "armed_lifted"
  //     (capture pipeline ended before it ever became recording — keep the
  //     visual available for the user to tap again)
  //   - any other state: no-op (stale callback from an earlier session)
  //
  // Concurrent stop protection: a stale capture-finished notification from
  // a previous recording session (e.g. recorder onerror firing after a new
  // recording has already started) MUST NOT collapse the new session's
  // recording state. We compare against the startRecording sequence id.
  const startRecordingSeqRef = useRef(0);
  const currentRecordingSeqRef = useRef(0);

  const notifyCaptureFinished = useCallback(
    (reason: "user_stop" | "silence" | "no_speech" | "error" | "max") => {
      // No-op when the bridge isn't wired (e.g. tests, or recorder hook
      // unmounted). The callback is allowed to fire from any code path.
      if (!onCaptureFinishedRef.current) return;
      const trigger = `recorder_capture_${reason}`;
      const currentSeq = currentRecordingSeqRef.current;
      const result = extractionResultRef.current;
      console.log(
        "[SUBJECT_LIFT] capture_finished_notify",
        { reason, currentSeq },
      );

      if (liftStateRef.current.status === "recording") {
        // For non-user_stop reasons (silence, no_speech, max, error), only allow
        // releasing if the user has already released the pointer. In hold-to-record
        // mode, abnormal stop triggers are IGNORED while the user is still holding.
        // This prevents silence/no_speech/max timers from ending a recording
        // that the user wants to continue.
        if (
          reason !== "user_stop" &&
          pointerHeldRef.current
        ) {
          console.log(
            "[SUBJECT_LIFT] capture_finished ignored: abnormal stop while pointer held",
            { reason, pointerHeld: pointerHeldRef.current },
          );
          return;
        }

        if (!result) {
          console.log(
            "[SubjectLift] capture_finished but no extraction result; collapsing to idle",
          );
          clearArmedTimeout();
          hasEnteredRecordingRef.current = false;
          setHasEnteredRecording(false);
          transitionState({ status: "idle" });
          return;
        }
        console.log("[SUBJECT_LIFT] releasing", { trigger, currentSeq });
        console.log("[subject_lift_transition]", {
          from: "recording",
          to: "releasing",
          reason: `capture_finished:${reason}`,
          sessionId: pressSessionIdRef.current,
          seq: currentSeq,
        });
        console.log(`[subject_lift_release] reason=${trigger} status=recording`);
        clearArmedTimeout();
        hasEnteredRecordingRef.current = false;
        setHasEnteredRecording(false);
        transitionState({ status: "releasing", ...result });
        return;
      }
      if (liftStateRef.current.status === "starting") {
        // Capture pipeline ended before recording was promoted. Roll back
        // so the user can tap again. We deliberately do NOT call
        // onStopRecordingRef because capture is already done — calling it
        // would be a no-op anyway.
        if (result) {
          console.log(
            "[SubjectLift] capture_finished while starting; rolling back to armed_lifted",
            { reason },
          );
          transitionState({ status: "armed_lifted", ...result });
        }
        return;
      }
      // Stale callback (already released, idle, error, or never recorded).
      console.log(
        "[SubjectLift] capture_finished ignored (stale)",
        { reason, status: liftStateRef.current.status, currentSeq },
      );
    },
    [clearArmedTimeout],
  );

  // Allow the layer to install the actual recorder start/stop hooks and capture-finished
  // callback. recorderStartRef gives doStartRecording a direct handle to call
  // recorder.start(ctx) with a frozen immutable context.
  const registerRecorderBridge = useCallback(
    (
      recorderStart: ((ctx: VoiceProcessingContext) => Promise<boolean>) | null,
      stopHook: (() => void) | null,
      captureFinishedHook:
        | ((
            reason: "user_stop" | "silence" | "no_speech" | "error" | "max",
          ) => void)
        | null = null,
    ) => {
      recorderStartRef.current = recorderStart;
      onStopRecordingRef.current = stopHook;
      onCaptureFinishedRef.current = captureFinishedHook;
    },
    [],
  );

  // Expose whether the lift has ever reached "recording" in this session.
  // We use a tick-based forceUpdate so consumers re-render when the flag
  // flips. The flag is read via getHasEnteredRecording() so React effects
  // can compare against previous render value.
  const [, forceRender] = useState(0);
  function getHasEnteredRecording(): boolean {
    return hasEnteredRecordingRef.current;
  }
  function setHasEnteredRecording(value: boolean): void {
    if (hasEnteredRecordingRef.current === value) return;
    hasEnteredRecordingRef.current = value;
    forceRender((n) => n + 1);
  }

  return {
    state,
    canLift,
    canLiftChecks,
    viewerRole,
    stopRecording,
    registerRecorderBridge,
    notifyCaptureFinished,
    hasEnteredRecording: getHasEnteredRecording(),
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
      onContextMenu,
    },
  };
}
