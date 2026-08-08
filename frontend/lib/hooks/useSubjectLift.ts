/**
 * useSubjectLift — long-press to "lift" a subject out of the wallpaper.
 *
 * State machine:
 *
 *   idle
 *     │ pointerdown (on partner region)
 *     ▼
 *   pressing ──500ms──▶ extracting ──API ok──▶ waiting_for_release
 *     │                      │                        │
 *     │                      └──API fail──────────────┘
 *     │ move>8px
 *     ▼
 *   idle
 *
 *   waiting_for_release
 *     │ pointerup (first release, extraction complete)
 *     ▼
 *   armed_lifted (10 second window)
 *     │ second tap on partner → recording (recording_pulsing)
 *     │ or timeout (10s)
 *     ▼
 *   releasing ──240ms──▶ idle
 *     │
 *     └── recording_pulsing → user click anywhere → finishRecording
 *
 * Permission logic (shared wallpaper fixed layout):
 *   upper = child, lower = elder
 *   elder viewer → partner = child → partnerRegion = upper
 *   child viewer → partner = elder → partnerRegion = lower
 *
 * Only the partner region can be lifted. The viewer's own region
 * (self) cannot be lifted.
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
import { getWallpaperDisplayGeometry } from "@/lib/wallpaperDisplayGeometry";
import {
  getSelectedWallpaper,
  isLatestWallpaper,
  useSceneStore,
  getWallpaperInteractionMode,
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
  wallpaperUrl: string;
  viewerRole: ViewerRole;
  personRole: PersonRole;
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
const MAX_SUBJECT_CUTOUT_CACHE_ENTRIES = 8;
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
 * Build a unique cache key for a partner cutout.
 * Uses URL as primary identity — each generated wallpaper has a unique immutable
 * filename, so the URL alone distinguishes revisions.  wallpaperVersion is no
 * longer part of the key to avoid stale mismatches during store sync races.
 */
export function buildSubjectCutoutCacheKey(params: {
  relationshipId: string;
  wallpaperUrl: string;
  viewerRole: ViewerRole;
  personRole: PersonRole;
  region: SubjectRegion;
}): string {
  const { relationshipId, wallpaperUrl, viewerRole, personRole, region } = params;
  return [
    relationshipId,
    wallpaperUrl,
    viewerRole,
    personRole,
    region,
  ].join("::");
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
    console.log("[subject_prefetch] action=cache_hit", {
      key,
      viewerRole: params.viewerRole,
      personRole: params.personRole,
      region: params.region,
    });
    return cached.result;
  }

  // 2. In-flight join — MUST check and set BEFORE the await to prevent
  // race conditions when two callers invoke simultaneously.
  const existingInFlight = subjectCutoutInFlight.get(key);
  if (existingInFlight) {
    console.log("[subject_prefetch] action=join_inflight", { key });
    const result = await existingInFlight;
    return result;
  }

  // 3. Fresh request — build the promise, store it synchronously, THEN await.
  // This ensures a second synchronous caller sees the in-flight entry immediately.
  console.log("[subject_prefetch] action=start", {
    key,
    viewerRole: params.viewerRole,
    personRole: params.personRole,
    region: params.region,
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

  // URL mismatch = wallpaper content changed
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
  if (!isLatestWallpaper({ currentDayIndex, currentWallpaperIndex, wallpapersByDay: currentWallpapersByDay })) {
    reasons.push("not_latest");
  }
  if (currentInsertStatus !== "ready") {
    reasons.push("insert_not_ready");
  }
  if (currentInteractionMode !== "subject_lift") {
    reasons.push("interaction_mode_mismatch");
    console.log("[subject_prefetch] interaction_mode_check", {
      currentInteractionMode,
      expected: "subject_lift",
    });
  }

  if (reasons.length > 0) {
    console.log("[subject_prefetch] action=stale_ignored", { key, staleReason: reasons });
    throw new StaleSubjectCutoutError(key, reasons);
  }

  // Prune before write to keep the Map bounded.
  pruneSubjectCutoutCache(params.relationshipId);

  // Write to module cache
  subjectCutoutCache.set(key, {
    result,
    key,
    relationshipId: params.relationshipId,
    wallpaperUrl: params.wallpaperUrl,
    viewerRole: params.viewerRole,
    personRole: params.personRole,
    region: params.region,
    createdAt: Date.now(),
  });

  return result;
}

// ── useSubjectLift ─────────────────────────────────────────────────────────────

// Long press threshold: 500ms
const LONG_PRESS_MS = 500;
// Movement tolerance during long press
const MOVE_TOLERANCE_PX = 8;
// How long an armed subject stays lifted before auto-releasing (10 seconds)
const ARMED_TIMEOUT_MS = 10_000;
// How long to show the releasing animation
const RELEASING_DURATION_MS = 260;
// Minimum gap between start-recording event and any stop-recording trigger
// to prevent the same pointer session from accidentally triggering stop.
const REC_START_STOP_GUARD_MS = 300;

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
  | { status: "waiting_for_release"; region: SubjectRegion }
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

  const { status: voiceStatus } = useWallpaperVoiceEditRecorder();

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
  const firstReleaseDoneRef = useRef(false);
  const armedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Guards the start-recording click from being immediately interpreted as
  // a stop-recording trigger by the global recording-stop listener.
  const recordingStartedAtRef = useRef(0);
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
  // Tracks whether the lift ever reached "recording" so we can tell real
  // recorder stop events apart from startup-phase idle -> recording flips.
  // This ref is mirrored into a tick-based state at the bottom of the hook
  // so React effects in the layer can subscribe to its changes.
  const hasEnteredRecordingRef = useRef(false);

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
  // Use the SAME isLatestWallpaper selector the rest of the app uses so
  // that older revisions of today are never eligible, regardless of URL.
  const isLatest = isLatestWallpaper({
    currentDayIndex,
    currentWallpaperIndex,
    wallpapersByDay,
  });
  const canLiftChecks = {
    uiModeIsWallpaper: uiMode === "wallpaper",
    isLatest,
    isToday: currentDayIndex === 2,
    insertReady: initialInsertStatus === "ready",
    voiceIdle: voiceStatus === "idle",
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
  useEffect(() => {
    // Gate: only prefetch when the subject-lift flow is available.
    if (!canLift) return;

    // Must have relationship context.
    if (!relationshipId) return;

    // Determine the partner region for this viewer.
    const region = getPartnerRegion(viewerRole);
    const personRole = getPartnerRole(viewerRole);
    const anchor = getRegionAnchor(region);

    const key = buildSubjectCutoutCacheKey({
      relationshipId,
      wallpaperUrl: generatedWallpaperUrl,
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
      wallpaperUrl: generatedWallpaperUrl,
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
      });
    });
  }, [
    // Re-trigger when the wallpaper identity changes (URL is the primary key).
    generatedWallpaperUrl,
    relationshipId,
    viewerRole,
    // canLift aggregates everything else; re-run when it becomes true.
    canLift,
  ]);

  // ── Cleanup helpers ─────────────────────────────────────────────────
  const cancelPress = useCallback(() => {
    pressSessionIdRef.current = 0;
    recognizedSessionIdRef.current = null;
    // Note: in-flight module-level requests are NOT aborted here.
    // They complete and are guarded by the stale check.
  }, []);

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
    transitionState({ status: "releasing", cutoutUrl, bbox, region });
  }, [clearArmedTimeout]);

  // ── Try to enter armed_lifted ────────────────────────────────────────
  // Only enters if BOTH extraction is complete AND first release has happened
  const tryEnterArmedLifted = useCallback(() => {
    const result = extractionResultRef.current;
    const prevStatus = state.status;
    if (!result) {
      console.log("[SubjectLift] tryEnterArmedLifted: no extraction result");
      return;
    }
    if (!firstReleaseDoneRef.current) {
      console.log("[SubjectLift] tryEnterArmedLifted: waiting for first release");
      return;
    }

    console.log("[SubjectLift] entering armed_lifted", {
      cutoutUrl: result.cutoutUrl.substring(0, 50) + "...",
      bbox: result.bbox,
      region: result.region,
    });

    // Start the 10 second armed timer — this fires only after BOTH
    // extraction success and the first pointerup. If extraction finished
    // first (race A), this is reached via pointerup. If pointerup came
    // first (race B), this is reached via runExtraction success.
    clearArmedTimeout();
    armedTimeoutRef.current = setTimeout(() => {
      console.log(
        `[subject_lift_release] reason=armed_timeout status=${prevStatus}`,
      );
      if (extractionResultRef.current) {
        enterReleasing(
          extractionResultRef.current.cutoutUrl,
          extractionResultRef.current.bbox,
          extractionResultRef.current.region,
        );
      }
    }, ARMED_TIMEOUT_MS);

    console.log("[subject_lift_armed]", {
      timeout_ms: ARMED_TIMEOUT_MS,
      region: result.region,
      personRole: getPersonRoleByRegion(result.region),
    });

    console.log("[subject_lift_transition]", {
      from: prevStatus,
      to: "armed_lifted",
      reason: "extraction_success_and_first_release",
      sessionId: pressSessionIdRef.current,
      region: result.region,
      firstReleaseDone: firstReleaseDoneRef.current,
      hasExtractionResult: true,
    });

    transitionState({ status: "armed_lifted", ...result });
  }, [clearArmedTimeout, enterReleasing, state.status]);

  // ── Reset when lift feature becomes ineligible ────────────────────────
  useEffect(() => {
    if (!canLift) {
      cancelPress();
      clearArmedTimeout();
      extractionResultRef.current = null;
      firstReleaseDoneRef.current = false;
      setHasEnteredRecording(false);
      transitionState({ status: "idle" });
    }
  }, [canLift, cancelPress, clearArmedTimeout]);

  // ── Releasing timeout ────────────────────────────────────────────────
  useEffect(() => {
    if (state.status !== "releasing") return;
    const id = window.setTimeout(() => {
      extractionResultRef.current = null;
      firstReleaseDoneRef.current = false;
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

    const relationshipId = useOnboardingStore.getState().userContext?.relationshipId ?? "";

    console.log("[api.extractSubject] start", {
      imageUrl: activeWallpaperUrl,
      pointX: pt.normalizedX,
      pointY: pt.normalizedY,
      region,
      viewerRole,
    });

    let result: SubjectExtractionResult | null = null;
    let source: "cache" | "joined_prefetch" | "live_request" = "live_request";

    try {
      // Use the shared helper: cache hit → immediate; in-flight join → no dup;
      // live → POST.  We still use the user's actual normalized click point.
      result = await getOrRequestPartnerCutout({
        relationshipId,
        wallpaperUrl: activeWallpaperUrl,
        viewerRole,
        personRole: getPartnerRole(viewerRole),
        region,
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
    const key = buildSubjectCutoutCacheKey({
      relationshipId,
      wallpaperUrl: activeWallpaperUrl,
      viewerRole,
      personRole: getPartnerRole(viewerRole),
      region,
    });
    const wasCached = subjectCutoutCache.has(key);
    source = wasCached ? "cache" : "live_request";

    console.log("[subject_lift_extraction]", {
      source,
      sessionId,
      region,
      cutoutUrl: result.cutoutUrl.substring(0, 50) + "...",
      bbox: result.bbox,
    });

    // Validate the press session is still active before accepting this result.
    // Even though the wallpaper/version checks passed, the user may have:
    //   - cancelled the long press
    //   - moved too far
    //   - triggered another interaction
    //   - changed viewer role mid-session
    // If any of these happened, discard the result silently.
    // Determine which states are valid to receive this result.
    // Race A: extraction completes first → still in "extracting"
    // Race B: user releases first → already in "waiting_for_release"
    // Both are valid. The result should NOT be discarded just because
    // status === "waiting_for_release" — that is the normal release-first race.
    // Only discard on genuinely invalid states:
    //   idle / pressing / armed_lifted / recording / releasing / error
    //   OR a NEWER session (different sessionId)
    //   OR a region mismatch in waiting_for_release
    // Use the synchronous ref instead of the stale closure `state.status`.
    // Cache hits and in-flight joins resolve synchronously (Promise already settled),
    // so the closure `state` is still at its pre-await value (e.g. "pressing").
    const currentStatus = liftStateRef.current.status;
    const isStaleSession =
      recognizedSessionIdRef.current !== sessionId ||
      (currentStatus !== "extracting" &&
        currentStatus !== "waiting_for_release") ||
      (currentStatus === "waiting_for_release" &&
        (liftStateRef.current as Extract<LiftState, { status: "waiting_for_release" }>).region !== region);

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
      firstReleaseDone: firstReleaseDoneRef.current,
      bbox: result.bbox,
    });

    // Transition to waiting_for_release
    console.log("[subject_lift_transition]", {
      from: "extracting",
      to: "waiting_for_release",
      sessionId,
      region,
      firstReleaseDone: firstReleaseDoneRef.current,
      hasExtractionResult: true,
    });
    transitionState({ status: "waiting_for_release", region });

    // If first release already happened, immediately enter armed_lifted
    if (firstReleaseDoneRef.current) {
      tryEnterArmedLifted();
    }
  }, [activeWallpaperUrl, imageSize, viewerRole, tryEnterArmedLifted]);

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

      // ── Start long press timer ────────────────────────────────────────
      // Reset the prior session's release/cutout state BEFORE bumping
      // pressSessionIdRef so the next press can't inherit a stale
      // firstReleaseDone or extractionResult from the previous session.
      cancelPress();
      firstReleaseDoneRef.current = false;
      extractionResultRef.current = null;
      setHasEnteredRecording(false);
      const sessionId = ++pressSessionIdRef.current;

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
  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      console.log(
        "[SubjectLift] pointerup",
        JSON.stringify({
          status: state.status,
          hasExtractionResult: !!extractionResultRef.current,
          firstReleaseDone: firstReleaseDoneRef.current,
        }),
      );

      // If we were in pressing state, cancel
      if (state.status === "pressing") {
        cancelPress();
        transitionState({ status: "idle" });
        return;
      }

      // If we were in extracting state, do NOT cancel the in-flight
      // extraction. The long press has already been recognized and
      // release here is expected user behavior (race B).
      //
      // Keep the fetch alive and mark firstReleaseDone so that when
      // runExtraction's success path runs, it can promote straight to
      // armed_lifted via the tryEnterArmedLifted() call at the end of
      // runExtraction.
      if (state.status === "extracting") {
        firstReleaseDoneRef.current = true;

        console.log("[subject_lift_release_while_extracting]", {
          sessionId: recognizedSessionIdRef.current,
          extractionInFlight: extractionInFlightRef.current,
          region: (state as Extract<LiftState, { status: "extracting" }>).region,
        });

        return;
      }

      // If we are in waiting_for_release state, mark first release done
      if (state.status === "waiting_for_release") {
        firstReleaseDoneRef.current = true;
        tryEnterArmedLifted();
        return;
      }

      // If we are in armed_lifted state, this is a second tap - handled by layer
      // Just log for debugging
      if (state.status === "armed_lifted") {
        console.log("[SubjectLift] pointerup while armed_lifted (second tap)");
        return;
      }

      // If we are in releasing state, ignore
      if (state.status === "releasing") {
        return;
      }
    },
    [state, cancelPress, tryEnterArmedLifted],
  );

  // ── Pointer cancel handler ────────────────────────────────────────────
  // Note: pointercancel is treated as a true cancellation — it aborts
  // any in-flight extraction and resets the session. pointerup is the
  // "user released the press" event and must NOT cancel the in-flight
  // extraction (race B handling lives in onPointerUp's "extracting"
  // branch).
  const onPointerCancel = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
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
        enterReleasing(
          extractionResultRef.current.cutoutUrl,
          extractionResultRef.current.bbox,
          extractionResultRef.current.region,
        );
      } else {
        extractionResultRef.current = null;
        firstReleaseDoneRef.current = false;
        setHasEnteredRecording(false);
        transitionState({ status: "idle" });
      }
    },
    [state, cancelPress, enterReleasing],
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

  // ── Start recording (called by layer on second tap) ─────────────────
  // Clears the 10-second armed timeout, transitions through "starting" while
  // awaiting the real recorder pipeline, and only enters "recording" once
  // the pipeline is verified live. If the pipeline fails, the state falls
  // back to "armed_lifted" so the subject visual stays lifted and the user
  // can retry — never a phantom recorder.
  const startRecording = useCallback(
    async (viewerRole: ViewerRole): Promise<boolean> => {
      if (state.status !== "armed_lifted") {
        console.log(
          "[SubjectLift] startRecording blocked: not armed_lifted",
          state.status,
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
        reason: "user_second_tap",
        sessionId: pressSessionIdRef.current,
      });
      transitionState({ status: "starting", ...result });

      // Await real recorder readiness. The bridge contract is Promise<boolean>.
      const startHook = onStartRecordingRef.current;
      if (!startHook) {
        console.warn(
          "[SubjectLift] startRecording aborted: no recorder bridge registered",
        );
        // Roll back to armed_lifted so the user can tap again.
        transitionState({ status: "armed_lifted", ...result });
        return false;
      }

      let ok = false;
      try {
        ok = await startHook(viewerRole);
      } catch (err) {
        console.error("[SubjectLift] recorder bridge threw", err);
        ok = false;
      }

      if (!ok) {
        console.log(
          "[SUBJECT_LIFT] recorder_start_failed returning to armed_lifted",
          {
            viewerRole,
          },
        );
        // Roll back to armed_lifted — do NOT animate the cutout away.
        transitionState({ status: "armed_lifted", ...result });
        return false;
      }

      hasEnteredRecordingRef.current = true;
      setHasEnteredRecording(true);
      console.log("[SUBJECT_LIFT] recording", {
        region: result.region,
        viewerRole,
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
    [state.status, clearArmedTimeout],
  );

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

  // Allow the layer to install the actual recorder start/stop hooks
  // without re-creating our callbacks. The recorder owns the long-lived
  // MediaRecorder instance.
  const registerRecorderBridge = useCallback(
    (
      startHook: ((viewerRole: ViewerRole) => Promise<boolean>) | null,
      stopHook: (() => void) | null,
    ) => {
      onStartRecordingRef.current = startHook;
      onStopRecordingRef.current = stopHook;
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
    startRecording,
    stopRecording,
    registerRecorderBridge,
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
