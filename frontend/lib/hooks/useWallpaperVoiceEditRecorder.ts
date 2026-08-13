/**
 * Voice recorder for the reference UI connected to the 2027 backend.
 *
 * The initial central-button voice keeps the backend's first-voice upload
 * contract. Subsequent subject-lift edits stream 16 kHz mono PCM packets every
 * 200 ms to the backend-owned ChatBot ASR WebSocket, with whole-file fallback.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";

import {
  currentWallpaperVoiceStreamUrl,
  generateFirstVoiceWallpapers,
  getCurrentWallpaper,
  type FirstVoiceAgentRunResult,
  updateCurrentWallpaperFromVoice,
  updateSessionProgress,
} from "@/lib/api";
import {
  getWallpaperInteractionMode,
  isLatestWallpaper,
  useSceneStore,
} from "./useSceneStore";
import {
  useAppPreferencesStore,
  type AppLanguage,
} from "@/lib/hooks/useAppPreferencesStore";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { dictionaries } from "@/lib/i18n";

export type WallpaperVoiceEditStatus =
  | "idle"
  | "recording"
  | "transcribing"
  | "editing";

export type ViewerRole = "child" | "elder";
export type VoiceAsrMode = "flash" | "stream";
export type RecorderSource = "central_button" | "subject_lift";

/**
 * Immutable context for one voice capture → processing pipeline. Created once
 * at capture start and threaded through every async stage so that:
 *   - A and B running concurrently cannot poison each other's job tracking
 *   - Generation success events carry the exact captureId that produced them
 *   - No mutable global state is read inside async pipelines
 */
export type VoiceProcessingContext = Readonly<{
  captureId: string;
  relationshipId: string;
  isFromHistoricalPage: boolean;
  source: RecorderSource;
}>;

const STREAM_SAMPLE_RATE = 16_000;
const STREAM_CHUNK_MS = 200;
const STREAM_CHUNK_SAMPLES = (STREAM_SAMPLE_RATE * STREAM_CHUNK_MS) / 1000;
const MIN_BLOB_BYTES = 2_000;

/**
 * Minimum wall-clock time after the recorder actually became ready before
 * silence detection is allowed to auto-stop a recording.
 *
 * Rationale: the first ~1.5s after microphone / WebSocket / AudioContext
 * are ready is usually calibration noise (warmup, echo cancellation
 * settling, sample-rate conversion). Auto-stopping in that window would
 * truncate legitimate speech on mobile devices with slow permission
 * flows.
 */
const MIN_RECORDING_MS = 1_500;

/**
 * Default target duration of trailing silence that ends a recording.
 * ~2.5 seconds of continuous silence is long enough to absorb natural
 * pauses between sentences (Chinese punctuation thinking time, breathing)
 * but short enough to feel responsive.
 */
const SILENCE_END_TARGET_MS = 2_500;

type VoiceProfile = {
  initialEndSilenceMs: number;
  minEndSilenceMs: number;
  maxEndSilenceMs: number;
  minSpeechMs: number;
  maxRecordingMs: number;
  noSpeechTimeoutMs: number;
  minRms: number;
};

const VOICE_PROFILES: Record<ViewerRole, VoiceProfile> = {
  child: {
    initialEndSilenceMs: 2_000,
    minEndSilenceMs: 1_500,
    maxEndSilenceMs: 3_000,
    minSpeechMs: 300,
    maxRecordingMs: 20_000,
    noSpeechTimeoutMs: 6_000,
    minRms: 0.012,
  },
  elder: {
    initialEndSilenceMs: 2_500,
    minEndSilenceMs: 1_500,
    maxEndSilenceMs: 3_000,
    minSpeechMs: 400,
    maxRecordingMs: 30_000,
    noSpeechTimeoutMs: 6_000,
    minRms: 0.008,
  },
};

type VoiceRuntimeState = {
  status: WallpaperVoiceEditStatus;
  mode: VoiceAsrMode;
  setStatus: (status: WallpaperVoiceEditStatus) => void;
  setMode: (mode: VoiceAsrMode) => void;
};

const useVoiceRuntimeStore = create<VoiceRuntimeState>((set) => ({
  status: "idle",
  mode: "stream",
  setStatus: (status) => set({ status }),
  setMode: (mode) => set({ mode }),
}));

export function setWallpaperVoiceEditStatus(status: WallpaperVoiceEditStatus) {
  useVoiceRuntimeStore.getState().setStatus(status);
}

export function useWallpaperVoiceEditRecorder() {
  const status = useVoiceRuntimeStore((state) => state.status);
  const mode = useVoiceRuntimeStore((state) => state.mode);
  const setStatus = useVoiceRuntimeStore((state) => state.setStatus);
  const setRuntimeMode = useVoiceRuntimeStore((state) => state.setMode);
  const [elapsedSec, setElapsedSec] = useState(0);
  // Reactive mirrors of the lifecycle refs so React effects (Subject Lift
  // canLift gate) can re-render when they change. The hook still owns the
  // authoritative truth in captureBusyRef / processingJobsRef.
  const [captureBusyState, setCaptureBusyState] = useState(false);
  const [processingJobCountState, setProcessingJobCountState] = useState(0);

  const setGeneratedWallpaperUrl = useSceneStore(
    (state) => state.setGeneratedWallpaperUrl,
  );
  const setInitialInsertStatus = useSceneStore(
    (state) => state.setInitialInsertStatus,
  );
  const setWallpaperInteractions = useSceneStore(
    (state) => state.setWallpaperInteractions,
  );

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const flashChunksRef = useRef<Blob[]>([]);
  const streamSocketRef = useRef<WebSocket | null>(null);
  const streamMediaRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const pendingPcmRef = useRef(new Int16Array(0));
  const allPcmRef = useRef<Int16Array[]>([]);
  const streamFinishedRef = useRef(false);
  const streamAsrFinalRef = useRef(false);
  const endingRef = useRef(false);
  const startedAtRef = useRef(0);
  const lastSpeechAtRef = useRef(0);
  const speechStartedAtRef = useRef(0);
  const noiseFloorRef = useRef(0.004);
  const observedPausesRef = useRef<number[]>([]);
  // Coalesce silence log spam: only log when silence state flips, or at most
  // once per 1.5s of continuous silence.
  const lastSilenceLogAtRef = useRef(0);
  const lastSilenceActiveRef = useRef(false);
  const adaptiveEndSilenceRef = useRef(750);
  const timerRef = useRef<number | null>(null);
  const maxTimerRef = useRef<number | null>(null);
  const noSpeechTimerRef = useRef<number | null>(null);
  // Wall-clock timestamp recorded the moment the real recorder pipeline
  // (microphone + WebSocket + AudioContext) became ready. All timing-based
  // controls (silence auto-stop, no-speech timeout, MIN_RECORDING_MS guard)
  // MUST measure from this point — not from the moment the user clicked.
  // On mobile, getUserMedia + WebSocket open + AudioContext.resume() can
  // take 500ms–2s.
  const actualRecorderReadyAtRef = useRef(0);
  const viewerRoleRef = useRef<ViewerRole>("child");
  // Microphone-capture busy flag. Set the instant a real recorder pipeline
  // (MediaRecorder / WebSocket) starts; cleared the moment the recorder
  // fully releases its hardware/socket resources, regardless of whether
  // the ASR / HTTP / image-generation pipeline is still running.
  const captureBusyRef = useRef(false);
  // Generation pipeline jobs. Each capture allocates a stable jobId that
  // survives across the microphone-capture window and is removed only by
  // that pipeline's own finally block. Multiple jobs may coexist when
  // the user starts a new capture while the previous generation is still
  // running.
  const processingJobsRef = useRef<Set<string>>(new Set());
  // Per-capture jobId allocator. Set the moment a real capture begins so
  // the ASR / HTTP / image-generation pipeline can register itself and
  // remove itself on completion.
  const processingJobIdRef = useRef<string>("");
  // When true, the silence / no_speech / max auto-stop timers do NOT call
  // finish*Recording for subject_lift captures. This prevents the recorder
  // from stopping the microphone while the user is still holding the pointer —
  // in hold-to-record mode, only a real pointerUp should end the capture.
  // Reset at the start of every new capture.
  const suppressSubjectLiftAutoStopRef = useRef(false);
  const requestIdRef = useRef("");
  const activeSourceRef = useRef<RecorderSource>("subject_lift");
  // Stable immutable context for the current capture. Written once at
  // capture start (when microphone permission is granted), read by every
  // async pipeline stage thereafter. No mutable global state is read inside
  // async callbacks — only this frozen snapshot.
  const activeContextRef = useRef<VoiceProcessingContext | null>(null);
  // Public getter so external consumers (e.g. Subject Lift) can read
  // the current captureId without bypassing the recorder hook's
  // lifecycle.
  const getCurrentCaptureId = useCallback(
    (): string => processingJobIdRef.current,
    [],
  );

  // Synchronize the lifecycle refs into React state. Centralized so every
  // path that mutates captureBusyRef or processingJobsRef only has to
  // call one function — and so the existing mutation sites stay close
  // to the surrounding logic.
  //
  // The setters are stored in refs because the lifecycle sync is invoked
  // from inside useCallback bodies whose closures capture an older
  // setCaptureBusyState / setProcessingJobCountState identity otherwise.
  const setCaptureBusyStateRef = useRef(setCaptureBusyState);
  setCaptureBusyStateRef.current = setCaptureBusyState;
  const setProcessingJobCountStateRef = useRef(setProcessingJobCountState);
  setProcessingJobCountStateRef.current = setProcessingJobCountState;
  const syncLifecycleState = () => {
    setCaptureBusyStateRef.current(captureBusyRef.current);
    setProcessingJobCountStateRef.current(processingJobsRef.current.size);
  };

  // Safe status transition. When the current capture's pipeline finishes,
  // we only set status to "idle" if there are no other processing jobs running.
  // This prevents A's finally from overwriting B's "editing"/"transcribing" status
  // when captures overlap.
  const safeSetIdleIfNoOtherJobs = (currentJobId: string) => {
    const otherJobs = Array.from(processingJobsRef.current).filter(
      (id) => id !== currentJobId,
    );
    if (otherJobs.length === 0 && !captureBusyRef.current) {
      setStatus("idle");
    }
  };

  // External listeners (e.g. Subject Lift) that want to know the exact
  // moment real audio capture stops. We fire on user stop, silence auto
  // stop, no-speech timeout, max-duration timeout, recorder onerror, and
  // flash fallback. The listeners run BEFORE the asynchronous ASR /
  // voice edit / image generation pipeline, so consumers can release
  // any visual state that should NOT persist across the full task.
  const captureFinishedListenersRef = useRef<
    Set<(reason: "user_stop" | "silence" | "no_speech" | "error" | "max") => void>
  >(new Set());

  // Listeners for generation success: called with the captureId / eventSeq
  // when a voice interaction successfully generates a new wallpaper
  // revision. Subject Lift uses this to set expectedJumpEventSeq for
  // historical page auto-jump. The captureId lets listeners ignore events
  // from a different (older / newer) request when processing pipelines
  // overlap.
  const generationSuccessListenersRef = useRef<
    Set<
      (payload: {
        eventSeq: number;
        captureId: string;
        relationshipId: string;
        isFromHistoricalPage: boolean;
      }) => void
    >
  >(new Set());

  const emitGenerationSuccess = useCallback(
    (payload: {
      eventSeq: number;
      captureId: string;
      relationshipId: string;
      isFromHistoricalPage: boolean;
    }) => {
      const listeners = generationSuccessListenersRef.current;
      if (!listeners.size) return;
      const snapshot = Array.from(listeners);
      for (const listener of snapshot) {
        try {
          listener(payload);
        } catch (err) {
          console.error("[VOICE] generationSuccess listener threw", err);
        }
      }
    },
    [],
  );

  const emitCaptureFinished = useCallback(
    (reason: "user_stop" | "silence" | "no_speech" | "error" | "max") => {
      const listeners = captureFinishedListenersRef.current;
      if (!listeners.size) return;
      // Copy to a snapshot so listeners can unsubscribe during iteration.
      const snapshot = Array.from(listeners);
      for (const listener of snapshot) {
        try {
          listener(reason);
        } catch (err) {
          console.error(
            "[VOICE] captureFinished listener threw",
            err,
          );
        }
      }
    },
    [],
  );

  const clearTimers = useCallback(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    if (maxTimerRef.current !== null) window.clearTimeout(maxTimerRef.current);
    if (noSpeechTimerRef.current !== null)
      window.clearTimeout(noSpeechTimerRef.current);
    timerRef.current = null;
    maxTimerRef.current = null;
    noSpeechTimerRef.current = null;
    actualRecorderReadyAtRef.current = 0;
  }, []);

  const applyAgentResult = useCallback(
    (
      response: FirstVoiceAgentRunResult,
      _durationMs: number,
      ctx: VoiceProcessingContext,
    ): boolean => {
      const result = response.result;
      const views = result.wallpaperViews;
      const viewerRole = viewerRoleRef.current;
      const wallpaperUrl =
        viewerRole === "elder"
          ? views?.elderViewUrl || ""
          : views?.childViewUrl || "";
      const lang: AppLanguage = useAppPreferencesStore.getState().language;
      const transcript =
        result.chatBot?.transcript?.trim() ||
        result.languageEmotion?.transcript?.trim() ||
        (dictionaries[lang]["voiceEdit.voiceUpdate"] as string);
      const eventSeq = response.eventSeq ?? 0;
      setWallpaperInteractions([
        {
          eventId: response.eventId || `event-${eventSeq}`,
          eventSeq,
          status: response.status,
          transcript,
          reply: result.chatBot?.reply?.trim() || "",
          speakerRole: viewerRole,
          createdAt: new Date().toISOString(),
        },
      ]);
      if (response.status === "queued" || result.status === "queued") {
        return false;
      }
      if (!wallpaperUrl) throw new Error(`Missing ${viewerRole} wallpaper view`);
      setGeneratedWallpaperUrl(wallpaperUrl);
      // Emit generation success with full immutable context for request-scoped
      // attribution. Uses the ctx parameter (frozen at processFlashRecording entry)
      // rather than activeContextRef to avoid race conditions with concurrent captures.
      if (eventSeq > 0) {
        emitGenerationSuccess({
          eventSeq,
          captureId: ctx.captureId,
          relationshipId: ctx.relationshipId,
          isFromHistoricalPage: ctx.isFromHistoricalPage,
        });
      }
      return true;
    },
    [setGeneratedWallpaperUrl, setWallpaperInteractions],
  );

  const processFlashRecording = useCallback(
    async (
      blob: Blob,
      durationMs: number,
      forcedCtx?: VoiceProcessingContext | null,
      forcedJobId?: string,
    ) => {
      // Use forced values if provided (from onstop callback with frozen context/jobId).
      // Otherwise freeze at processing-start so concurrent captures cannot overwrite
      // it before async cleanup runs.
      const ctx = forcedCtx !== undefined ? forcedCtx : activeContextRef.current;
      // Frozen jobId: always use forcedJobId if provided; otherwise freeze from ref.
      // Async cleanup must NEVER rely on processingJobIdRef.current which may have
      // been overwritten by a subsequent capture.
      const jobId = forcedJobId ?? processingJobIdRef.current;
      if (!ctx) {
        console.error("[WallpaperVoiceEdit] processFlashRecording: no active context");
        captureBusyRef.current = false;
        processingJobsRef.current.delete(jobId);
        syncLifecycleState();
        setStatus("idle");
        return;
      }
      const profile = VOICE_PROFILES[viewerRoleRef.current];
      if (durationMs < profile.minSpeechMs || blob.size < MIN_BLOB_BYTES) {
        console.warn("[WallpaperVoiceEdit] recording too short, please retry");
        captureBusyRef.current = false;
        processingJobsRef.current.delete(jobId);
        syncLifecycleState();
        setStatus("idle");
        return;
      }
      processingJobsRef.current.add(jobId);
      syncLifecycleState();
      setStatus("editing");
      let queued = false;
      try {
        const source = activeSourceRef.current;
        if (source === "central_button") {
          setInitialInsertStatus("generating");
          const response = await generateFirstVoiceWallpapers(blob);
          const completed = applyAgentResult(response, durationMs, ctx);
          if (!completed) {
            queued = true;
          } else {
            const viewerRole = viewerRoleRef.current;
            const views = response.result.wallpaperViews;
            const wallpaperUrl =
              viewerRole === "elder"
                ? views?.elderViewUrl || ""
                : views?.childViewUrl || "";
            setInitialInsertStatus("ready");
            await updateSessionProgress({
              onboardingStep: "wallpaper",
              wallpaperUrl,
            });
          }
        } else {
          const response = await updateCurrentWallpaperFromVoice(
            blob,
            requestIdRef.current || crypto.randomUUID(),
          );
          queued = !applyAgentResult(response, durationMs, ctx);
        }
      } catch (error) {
        console.error(
          "[WallpaperVoiceEdit] flash pipeline failed:",
          error instanceof Error ? error.message : String(error),
        );
        if (activeSourceRef.current === "central_button") {
          setInitialInsertStatus("failed");
        }
      } finally {
        processingJobsRef.current.delete(jobId);
        syncLifecycleState();
        if (!queued) safeSetIdleIfNoOtherJobs(jobId);
      }
    },
    [applyAgentResult, setInitialInsertStatus, setStatus],
  );

  const cleanupStreamingAudio = useCallback(() => {
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamMediaRef.current?.getTracks().forEach((track) => track.stop());
    void audioContextRef.current?.close().catch(() => undefined);
    processorRef.current = null;
    sourceRef.current = null;
    streamMediaRef.current = null;
    audioContextRef.current = null;
  }, []);

  const closeStreamSocket = useCallback(() => {
    const socket = streamSocketRef.current;
    streamSocketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
  }, []);

  const fallbackStreamToFlash = useCallback(
    (reason: string, jobId: string) => {
      if (streamAsrFinalRef.current || streamFinishedRef.current) return;
      streamFinishedRef.current = true;
      const durationMs = Date.now() - startedAtRef.current;
      const wav = encodePcmWav(allPcmRef.current, STREAM_SAMPLE_RATE);
      console.warn(`[WallpaperVoiceEdit] streaming unavailable; using flash: ${reason}`);
      // Clear captureBusyRef: we are abandoning the stream pipeline and transitioning
      // to flash recording. Flash will set up its own MediaRecorder.
      captureBusyRef.current = false;
      processingJobsRef.current.delete(jobId);
      syncLifecycleState();
      clearTimers();
      cleanupStreamingAudio();
      closeStreamSocket();
      // Compare-and-clear: only clear if this is still the active capture.
      // An older callback arriving after a newer capture has already claimed the ref
      // must NOT overwrite the new capture's identity.
      if (processingJobIdRef.current === jobId) {
        processingJobIdRef.current = "";
      }
      void processFlashRecording(wav, durationMs);
    },
    [clearTimers, cleanupStreamingAudio, closeStreamSocket, processFlashRecording],
  );

  const flushStreamingPacket = useCallback((flushAll = false) => {
    const socket = streamSocketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    while (
      pendingPcmRef.current.length >= STREAM_CHUNK_SAMPLES ||
      (flushAll && pendingPcmRef.current.length > 0)
    ) {
      const size = Math.min(STREAM_CHUNK_SAMPLES, pendingPcmRef.current.length);
      const packet = pendingPcmRef.current.slice(0, size);
      pendingPcmRef.current = pendingPcmRef.current.slice(size);
      socket.send(packet.buffer);
    }
  }, []);

  const finishStreamingRecording = useCallback(
    (
      reason: "user_stop" | "silence" | "no_speech" | "max" | "error" = "user_stop",
      jobId?: string,
    ) => {
      if (endingRef.current || streamFinishedRef.current) return;
      endingRef.current = true;
      console.log("[VOICE] stop_requested reason=stream_finish", { reason });
      // Notify capture-finished listeners BEFORE we tear down the audio
      // graph / socket, so the Subject Lift visual can transition to
      // releasing immediately rather than waiting on the ASR / image
      // generation pipeline (which can take 30-60s on mobile).
      emitCaptureFinished(reason);
      clearTimers();
      cleanupStreamingAudio();
      flushStreamingPacket(true);
      setElapsedSec(0);

      const durationMs = Date.now() - startedAtRef.current;
      const profile = VOICE_PROFILES[viewerRoleRef.current];
      const socket = streamSocketRef.current;
      if (
        !socket ||
        socket.readyState !== WebSocket.OPEN ||
        durationMs < profile.minSpeechMs ||
        allPcmRef.current.length === 0
      ) {
        if (socket?.readyState === WebSocket.OPEN)
          socket.send(JSON.stringify({ type: "cancel" }));
        // In this early-return path we use processingJobIdRef.current directly as the
        // reference for compare-and-clear. Since this guard only fires when the recorder
        // is already inactive, it is by definition a stale cleanup path.
        const staleId = processingJobIdRef.current;
        closeStreamSocket();
        captureBusyRef.current = false;
        processingJobsRef.current.delete(staleId);
        if (processingJobIdRef.current === staleId) {
          processingJobIdRef.current = "";
        }
        syncLifecycleState();
        setStatus("idle");
        return;
      }
      socket.send(JSON.stringify({ type: "end" }));
      setStatus("transcribing");
      console.log("[VOICE] recorder_stopped", {
        mode: "stream",
        durationMs,
        reason,
      });
    },
    [
      clearTimers,
      cleanupStreamingAudio,
      closeStreamSocket,
      emitCaptureFinished,
      flushStreamingPacket,
      setStatus,
    ],
  );

  const finishFlashRecording = useCallback(
    (
      reason: "user_stop" | "silence" | "no_speech" | "max" | "error" = "user_stop",
      jobId?: string,
    ) => {
      const jobIdToUse = jobId ?? processingJobIdRef.current;
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state === "inactive") {
        // Already inactive: still notify so any visual state can collapse.
        // Complete cleanup so the next startRecording call is not blocked by stale state.
        emitCaptureFinished(reason);
        captureBusyRef.current = false;
        processingJobsRef.current.delete(jobIdToUse);
        // Compare-and-clear: only clear if this is still the active capture.
        if (processingJobIdRef.current === jobIdToUse) {
          processingJobIdRef.current = "";
        }
        syncLifecycleState();
        return;
      }
      console.log("[VOICE] stop_requested reason=flash_finish", { reason });
      // Notify capture-finished listeners synchronously so Subject Lift
      // can transition to releasing the moment MediaRecorder.stop() is
      // invoked. We deliberately do NOT wait for the async onstop —
      // otherwise the Subject Lift visual would persist until the
      // ASR / image-generation pipeline completes.
      emitCaptureFinished(reason);
      clearTimers();
      // Clear mediaRecorderRef immediately so a subsequent startRecording call
      // does not see a stale recorder and reject with "media_recorder_exists".
      mediaRecorderRef.current = null;
      const durationMs = Date.now() - startedAtRef.current;
      setElapsedSec(0);
      // Capture frozen values in the onstop closure so processFlashRecording
      // always uses the correct (this-capture) jobId and context — never
      // the mutable processingJobIdRef.current which may belong to a later capture.
      const capturedJobId = jobIdToUse;
      const capturedCtx = activeContextRef.current;
      recorder.onstop = () => {
        recorder.stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(flashChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        flashChunksRef.current = [];
        // Pass frozen jobId and ctx so processFlashRecording uses the correct
        // (this-capture) values regardless of what processingJobIdRef.current is now.
        void processFlashRecording(blob, durationMs, capturedCtx, capturedJobId);
      };
      try {
        recorder.requestData?.();
      } catch (error) {
        console.warn("[WallpaperVoiceEdit] requestData failed", error);
      }
      recorder.stop();
      setStatus("transcribing");
      console.log("[VOICE] recorder_stopped", {
        mode: "flash",
        durationMs,
        reason,
      });
    },
    [clearTimers, emitCaptureFinished, processFlashRecording, setStatus],
  );

  const startFlashRecording = useCallback(
    async (stream: MediaStream) => {
      // Freeze jobId so the onerror cleanup always uses the correct (this-capture) id.
      const thisCaptureJobId = processingJobIdRef.current;
      const preferredMime = "audio/webm;codecs=opus";
      const mimeType = window.MediaRecorder.isTypeSupported(preferredMime)
        ? preferredMime
        : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      flashChunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data?.size) flashChunksRef.current.push(event.data);
      };
      recorder.onerror = (event) => {
        console.error("[WallpaperVoiceEdit] MediaRecorder error", event);
        emitCaptureFinished("error");
        clearTimers();
        stream.getTracks().forEach((track) => track.stop());
        mediaRecorderRef.current = null;
        flashChunksRef.current = [];
        captureBusyRef.current = false;
        processingJobsRef.current.delete(thisCaptureJobId);
        // Compare-and-clear: only clear if this is still the active capture.
        if (processingJobIdRef.current === thisCaptureJobId) {
          processingJobIdRef.current = "";
        }
        syncLifecycleState();
        setStatus("idle");
      };
      recorder.start(STREAM_CHUNK_MS);
    },
    [clearTimers, emitCaptureFinished, setStatus],
  );

  const startStreamingRecording = useCallback(
    async (stream: MediaStream) => {
      // Freeze context at streaming-start so concurrent captures cannot overwrite
      // it before the async message handler reads it.
      const ctx = activeContextRef.current;
      // Freeze the capture id (for identity-comparison only — does NOT claim ownership).
      // We need this for safe compare-and-clear in the early ctx-null guard path,
      // which must NOT clobber a later capture's identity if this callback fires stale.
      const thisCaptureJobId = ctx?.captureId ?? processingJobIdRef.current;
      if (!ctx) {
        console.error("[WallpaperVoiceEdit] startStreamingRecording: no active context");
        closeStreamSocket();
        captureBusyRef.current = false;
        processingJobsRef.current.delete(thisCaptureJobId);
        // Compare-and-clear: only clear if this is still the active capture.
        if (processingJobIdRef.current === thisCaptureJobId) {
          processingJobIdRef.current = "";
        }
        syncLifecycleState();
        setStatus("idle");
        return;
      }
      // thisCaptureJobId is already frozen above from ctx.captureId — reuse it
      // for the message handler and all its cleanup paths.
      const profile = VOICE_PROFILES[viewerRoleRef.current];
      const socket = new WebSocket(currentWallpaperVoiceStreamUrl());
      streamSocketRef.current = socket;
      socket.binaryType = "arraybuffer";

      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(
          () => reject(new Error("streaming ASR connection timed out")),
          15_000,
        );
        socket.onopen = () => {
          console.log("[VOICE] websocket_ready");
        };
        socket.onmessage = (event) => {
          if (typeof event.data !== "string") return;
          const message = JSON.parse(event.data) as {
            type?: string;
            detail?: string;
            fallbackAllowed?: boolean;
            payload?: FirstVoiceAgentRunResult;
          };
          if (message.type === "ready") {
            socket.send(
              JSON.stringify({
                type: "start",
                requestId: requestIdRef.current || crypto.randomUUID(),
              }),
            );
          } else if (message.type === "started") {
            console.log("[VOICE] websocket_started_ack");
            window.clearTimeout(timeout);
            resolve();
          } else if (message.type === "asr_final") {
            streamAsrFinalRef.current = true;
            setStatus("editing");
          } else if (message.type === "final" && message.payload) {
            streamFinishedRef.current = true;
            const durationMs = Date.now() - startedAtRef.current;
            let queued = false;
            try {
              queued = !applyAgentResult(message.payload, durationMs, ctx);
            } finally {
              processingJobsRef.current.delete(thisCaptureJobId);
              syncLifecycleState();
              closeStreamSocket();
              if (!queued) safeSetIdleIfNoOtherJobs(thisCaptureJobId);
            }
          } else if (message.type === "error") {
            window.clearTimeout(timeout);
            const detail = message.detail || "streaming ASR failed";
            // Notify capture-finished listeners: capture has stopped (or
            // never really started) regardless of which branch below runs.
            emitCaptureFinished("error");
            if (
              message.fallbackAllowed !== false &&
              !streamAsrFinalRef.current &&
              allPcmRef.current.length > 0
            ) {
              fallbackStreamToFlash(detail, thisCaptureJobId);
            } else if (!streamAsrFinalRef.current && allPcmRef.current.length === 0) {
              closeStreamSocket();
            } else {
              console.error(`[WallpaperVoiceEdit] ${detail}`);
              captureBusyRef.current = false;
              processingJobsRef.current.delete(thisCaptureJobId);
              // Compare-and-clear: only clear if this is still the active capture.
              if (processingJobIdRef.current === thisCaptureJobId) {
                processingJobIdRef.current = "";
              }
              syncLifecycleState();
              clearTimers();
              cleanupStreamingAudio();
              closeStreamSocket();
              setStatus("idle");
            }
            reject(new Error(detail));
          }
        };
        socket.onerror = () => {
          window.clearTimeout(timeout);
          // Streaming transport error during capture: the audio pipeline
          // is gone, so emit capture-finished synchronously. The outer
          // catch in startRecording() will then either fall back to flash
          // (if no PCM was captured yet) or report failure.
          emitCaptureFinished("error");
          reject(new Error("streaming ASR WebSocket failed"));
        };
        socket.onclose = () => {
          window.clearTimeout(timeout);
          if (
            !streamFinishedRef.current &&
            !streamAsrFinalRef.current &&
            allPcmRef.current.length > 0
          ) {
            fallbackStreamToFlash("streaming connection closed", thisCaptureJobId);
          }
        };
      });

      const AudioContextClass =
        window.AudioContext ||
        (
          window as typeof window & {
            webkitAudioContext?: typeof AudioContext;
          }
        ).webkitAudioContext;
      if (!AudioContextClass) throw new Error("AudioContext is unavailable");
      const context = new AudioContextClass();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      streamMediaRef.current = stream;
      audioContextRef.current = context;
      sourceRef.current = source;
      processorRef.current = processor;

      processor.onaudioprocess = (event) => {
        if (streamFinishedRef.current || endingRef.current) return;
        const input = event.inputBuffer.getChannelData(0);
        const pcm = downsampleToPcm16(input, context.sampleRate, STREAM_SAMPLE_RATE);
        if (pcm.length) {
          allPcmRef.current.push(pcm);
          pendingPcmRef.current = concatInt16(pendingPcmRef.current, pcm);
          flushStreamingPacket();
        }

        const now = Date.now();
        const rms = calculateRms(input);
        const threshold = Math.max(profile.minRms, noiseFloorRef.current * 2.5);
        if (rms >= threshold) {
          // Speech resumed — cancel any pending silence log.
          if (lastSilenceActiveRef.current) {
            console.log("[VOICE] silence_cancelled");
            lastSilenceActiveRef.current = false;
            lastSilenceLogAtRef.current = 0;
          }
          if (lastSpeechAtRef.current > 0) {
            const pause = now - lastSpeechAtRef.current;
            if (pause >= 150 && pause < adaptiveEndSilenceRef.current) {
              const pauses = [...observedPausesRef.current, pause].slice(-12);
              observedPausesRef.current = pauses;
              const p90 = percentile(pauses, 0.9);
              adaptiveEndSilenceRef.current = clamp(
                p90 + 300,
                profile.minEndSilenceMs,
                profile.maxEndSilenceMs,
              );
            }
          }
          if (!speechStartedAtRef.current) speechStartedAtRef.current = now;
          lastSpeechAtRef.current = now;
        } else {
          if (!speechStartedAtRef.current) {
            noiseFloorRef.current = noiseFloorRef.current * 0.94 + rms * 0.06;
          } else if (
            // Only allow silence-triggered stop AFTER the recorder has
            // been actually recording for MIN_RECORDING_MS. This absorbs
            // warmup noise from microphone / AudioContext / WebSocket
            // pipeline that may look like a brief speech blip.
            Date.now() - actualRecorderReadyAtRef.current >= MIN_RECORDING_MS &&
            now - speechStartedAtRef.current >= profile.minSpeechMs &&
            now - lastSpeechAtRef.current >= adaptiveEndSilenceRef.current
          ) {
            // Guard: stale callback check — if this callback belongs to a previous capture
            // (processingJobIdRef.current was overwritten by a later capture), do nothing.
            if (processingJobIdRef.current !== thisCaptureJobId) return;
            // Guard: for subject_lift captures, suppress silence auto-stop so
            // the microphone keeps recording while the user holds the pointer.
            if (suppressSubjectLiftAutoStopRef.current) {
              // Subject Lift is active — do NOT stop recording on silence.
              // Log periodically to confirm the guard is live.
              console.log("[VOICE] silence_guard_active: not stopping for subject_lift");
              return;
            }
            // Logged once when silence-run actually triggers auto-stop.
            console.log("[VOICE] auto_stop_triggered", {
              reason: "silence",
              adaptiveMs: adaptiveEndSilenceRef.current,
            });
            finishStreamingRecording("silence");
          } else if (
            speechStartedAtRef.current &&
            Date.now() - actualRecorderReadyAtRef.current >= MIN_RECORDING_MS &&
            !lastSilenceActiveRef.current
          ) {
            // Edge-detected silence start (after MIN_RECORDING_MS guard).
            console.log("[VOICE] silence_started", {
              threshold: adaptiveEndSilenceRef.current,
            });
            lastSilenceActiveRef.current = true;
            lastSilenceLogAtRef.current = now;
          } else if (
            lastSilenceActiveRef.current &&
            now - lastSilenceLogAtRef.current >= 1_500
          ) {
            // Periodic keep-alive log while silence continues.
            console.log("[VOICE] silence_continuing", {
              elapsedMs: now - lastSpeechAtRef.current,
            });
            lastSilenceLogAtRef.current = now;
          }
        }
      };
      source.connect(processor);
      processor.connect(context.destination);
      console.log("[VOICE] audio_ready", {
        sampleRate: context.sampleRate,
        streamTracks: stream.getTracks().length,
      });
    },
    [
      applyAgentResult,
      cleanupStreamingAudio,
      clearTimers,
      closeStreamSocket,
      fallbackStreamToFlash,
      finishStreamingRecording,
      flushStreamingPacket,
      setStatus,
    ],
  );

  const startRecording = useCallback(
    async (ctx: VoiceProcessingContext): Promise<boolean> => {
      console.log("[VOICE] start_requested", { ctx });

      // ── Idempotent cleanup ─────────────────────────────────────────────
      // Only clean up stale state if capture is NOT currently active.
      // We must NOT stop a MediaRecorder or WebSocket that is legitimately
      // recording for THIS session. The captureBusyRef is set to true AFTER
      // all init refs are reset (line 987), so checking it here tells us
      // whether a real capture is in progress.
      if (!captureBusyRef.current) {
        // No active capture — safe to clean up any stale recorder/socket from
        // an abnormal previous stop (silence/timeout/error) that didn't finish cleanup.
        const staleRecorder = mediaRecorderRef.current;
        if (staleRecorder) {
          console.warn("[VOICE] startRecording: cleaning stale MediaRecorder");
          staleRecorder.onstop = null;
          try { staleRecorder.stop(); } catch { /* already stopped */ }
          staleRecorder.stream.getTracks().forEach((t) => t.stop());
          mediaRecorderRef.current = null;
        }
        const staleSocket = streamSocketRef.current;
        if (staleSocket) {
          console.warn("[VOICE] startRecording: closing stale WebSocket");
          staleSocket.onopen = null;
          staleSocket.onmessage = null;
          staleSocket.onerror = null;
          staleSocket.onclose = null;
          staleSocket.close();
          streamSocketRef.current = null;
        }
      }

      // Freeze the immutable context immediately. All async pipeline stages read
      // from activeContextRef — never from mutable global state.
      activeContextRef.current = ctx;
      // Freeze jobId immediately for all async cleanup paths in this capture.
      // Freeze capture identity from ctx.captureId — the single source of truth for
      // this capture. processingJobIdRef.current is set below after all guards pass,
      // so we use ctx.captureId directly here for the freeze.
      const thisCaptureJobId = ctx.captureId;
      // Only the real microphone-capture ref is a hard gate. Background
      // generation pipelines (ASR / HTTP / image-edit) do NOT block a
      // brand-new capture — they are tracked via processingJobsRef.
      if (captureBusyRef.current) {
        console.log("[VOICE] start_rejected: capture_busy");
        activeContextRef.current = null;
        return false;
      }
      if (mediaRecorderRef.current) {
        console.log("[VOICE] start_rejected: media_recorder_exists");
        activeContextRef.current = null;
        return false;
      }
      if (streamSocketRef.current) {
        console.log("[VOICE] start_rejected: stream_socket_exists");
        activeContextRef.current = null;
        return false;
      }
      const scene = useSceneStore.getState();
      // Subject Lift: allow recording on ANY wallpaper revision.
      // The cutout comes from the currently visible wallpaper;
      // the backend uses latest shared wallpaper for generation.
      if (ctx.source === "subject_lift") {
        if (!scene.generatedWallpaperUrl) {
          console.warn(
            "[VOICE] start_rejected: no_generated_wallpaper",
          );
          activeContextRef.current = null;
          return false;
        }
        // Central button: require initial_voice/processing mode
      } else {
        const interactionMode = getWallpaperInteractionMode(scene);
        if (
          interactionMode !== "initial_voice" &&
          interactionMode !== "processing"
        ) {
          console.warn(
            `[VOICE] start_rejected: ${ctx.source} mode=${interactionMode}`,
          );
          activeContextRef.current = null;
          return false;
        }
      }

      try {
        const current = await getCurrentWallpaper();
        if (ctx.source === "central_button" && current.stage === "wallpaper_active") {
          console.warn(
            "[VOICE] start_rejected: central button not allowed after first voice is done",
          );
          activeContextRef.current = null;
          return false;
        }
        if (ctx.source === "subject_lift" && current.stage !== "wallpaper_active") {
          console.warn(
            "[VOICE] start_rejected: subject lift not allowed before first voice",
          );
          activeContextRef.current = null;
          return false;
        }
      } catch (error) {
        console.error(
          "[VOICE] cannot reach backend stage, recording aborted",
          error,
        );
        activeContextRef.current = null;
        return false;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        console.error("[VOICE] browser does not support recording");
        activeContextRef.current = null;
        return false;
      }
      // Only after all guards pass do we claim the active-capture slot.
      // This is the sole permitted write to processingJobIdRef.current for
      // an active capture — it identifies which capture currently holds the microphone.
      processingJobIdRef.current = thisCaptureJobId;
      activeSourceRef.current = ctx.source;
      const requestedMode =
        ctx.source === "central_button"
          ? "flash"
          : useVoiceRuntimeStore.getState().mode;
      if (requestedMode === "flash" && !window.MediaRecorder) {
        console.error("[VOICE] browser does not support MediaRecorder");
        activeContextRef.current = null;
        return false;
      }

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        console.log("[VOICE] microphone_ready");
      } catch (error) {
        console.error("[VOICE] microphone permission denied", error);
        activeContextRef.current = null;
        return false;
      }

      const profile = VOICE_PROFILES[viewerRoleRef.current];
      startedAtRef.current = Date.now();
      // Mark "requested" so the stop-anywhere listener knows NOT to enter
      // recorder_error while we're still mid-startup. The true value will be
      // re-set right after the pipeline reports ready.
      actualRecorderReadyAtRef.current = 0;
      requestIdRef.current = crypto.randomUUID();
      lastSpeechAtRef.current = 0;
      speechStartedAtRef.current = 0;
      observedPausesRef.current = [];
      adaptiveEndSilenceRef.current = SILENCE_END_TARGET_MS;
      noiseFloorRef.current = 0.004;
      pendingPcmRef.current = new Int16Array(0);
      allPcmRef.current = [];
      streamFinishedRef.current = false;
      streamAsrFinalRef.current = false;
      endingRef.current = false;
      // Suppress silence/no_speech/max auto-stop when recording for subject_lift.
      // In hold-to-record mode the user controls when to stop via pointerUp.
      // Reset every capture so a non-subject_lift capture always gets normal auto-stop.
      suppressSubjectLiftAutoStopRef.current = ctx.source === "subject_lift";
      captureBusyRef.current = true;
      syncLifecycleState();
      setElapsedSec(0);

      let activeMode = requestedMode;
      try {
        if (activeMode === "stream") {
          await startStreamingRecording(stream);
        } else {
          await startFlashRecording(stream);
        }
      } catch (error) {
        if (activeMode === "stream" && allPcmRef.current.length === 0) {
          console.warn(
            "[VOICE] streaming could not start; recording with flash",
            error,
          );
          closeStreamSocket();
          activeMode = "flash";
          await startFlashRecording(stream);
        } else {
          console.error("[VOICE] pipeline start failed", error);
          stream.getTracks().forEach((track) => track.stop());
          captureBusyRef.current = false;
          processingJobsRef.current.delete(thisCaptureJobId);
          syncLifecycleState();
          // Compare-and-clear: only clear if this is still the active capture.
          // An older async callback that arrives after a new capture has already
          // claimed the ref must NOT overwrite the new capture's identity.
          if (processingJobIdRef.current === thisCaptureJobId) {
            processingJobIdRef.current = "";
          }
          if (!streamFinishedRef.current) {
            fallbackStreamToFlash(
              error instanceof Error ? error.message : String(error),
              thisCaptureJobId,
            );
          }
          return false;
        }
      }

      // Recorder pipeline is fully ready: microphone + WebSocket +
      // AudioContext + (MediaRecorder fallback). Anchor all timing-based
      // controls from THIS moment, NOT from the user click.
      actualRecorderReadyAtRef.current = Date.now();
      console.log("[VOICE] recording_started", {
        activeMode,
        readyAt: actualRecorderReadyAtRef.current,
      });
      setStatus("recording");
      timerRef.current = window.setInterval(() => {
        setElapsedSec(Math.round((Date.now() - startedAtRef.current) / 1000));
      }, 200);
      // Freeze the capture identity so stale timer callbacks cannot stop a later capture.
      const activeCaptureId = ctx.captureId;
      maxTimerRef.current = window.setTimeout(() => {
        // Guard: stale timer check — if this callback belongs to a previous capture
        // (ref.current was overwritten by a later capture), do nothing.
        if (processingJobIdRef.current !== activeCaptureId) return;
        // Guard: for subject_lift captures, suppress max-duration auto-stop so
        // the microphone keeps recording while the user holds the pointer.
        if (suppressSubjectLiftAutoStopRef.current) {
          console.log("[VOICE] max_timer_guard_active: not stopping for subject_lift");
          return;
        }
        if (activeMode === "stream") finishStreamingRecording("max");
        else finishFlashRecording("max");
      }, profile.maxRecordingMs);
      if (activeMode === "stream") {
        noSpeechTimerRef.current = window.setTimeout(() => {
          // Guard: stale timer check — if this callback belongs to a previous capture
          // (ref.current was overwritten by a later capture), do nothing.
          if (processingJobIdRef.current !== activeCaptureId) return;
          // Guard: for subject_lift captures, suppress no_speech auto-stop so
          // the microphone keeps recording while the user holds the pointer.
          if (suppressSubjectLiftAutoStopRef.current) {
            console.log("[VOICE] no_speech_timer_guard_active: not stopping for subject_lift");
            return;
          }
          if (!speechStartedAtRef.current) finishStreamingRecording("no_speech");
        }, profile.noSpeechTimeoutMs);
      }
      return true;
    },
    [
      fallbackStreamToFlash,
      finishFlashRecording,
      finishStreamingRecording,
      closeStreamSocket,
      setStatus,
      setRuntimeMode,
      startFlashRecording,
      startStreamingRecording,
    ],
  );

  const finishRecording = useCallback(() => {
    console.log("[VOICE] stop_requested reason=user_or_layer");
    if (streamSocketRef.current) finishStreamingRecording("user_stop");
    else finishFlashRecording("user_stop");
  }, [finishFlashRecording, finishStreamingRecording]);

  const toggle = useCallback(
    async (viewerRole?: ViewerRole) => {
      if (viewerRole) viewerRoleRef.current = viewerRole;
      if (status === "idle") {
        const ctx: VoiceProcessingContext = {
          captureId: crypto.randomUUID(),
          relationshipId:
            useOnboardingStore.getState().userContext?.relationshipId ?? "",
          isFromHistoricalPage: false,
          source: "central_button",
        };
        const ok = await startRecording(ctx);
        console.log("[VOICE] toggle result", { ok });
      } else if (status === "recording") {
        console.log("[VOICE] stop_requested reason=toggle");
        finishRecording();
      }
    },
    [finishRecording, startRecording, status],
  );

  const start = useCallback(
    async (ctx: VoiceProcessingContext): Promise<boolean> => {
      return startRecording(ctx);
    },
    [startRecording],
  );

  const setMode = useCallback(
    (nextMode: VoiceAsrMode) => {
      if (useVoiceRuntimeStore.getState().status === "idle")
        setRuntimeMode(nextMode);
    },
    [setRuntimeMode],
  );

  useEffect(() => {
    return () => {
      clearTimers();
      cleanupStreamingAudio();
      closeStreamSocket();
      const recorder = mediaRecorderRef.current;
      if (recorder) {
        recorder.onstop = null;
        if (recorder.state !== "inactive") {
          try {
            recorder.stop();
          } catch {
            // Already stopped.
          }
        }
        recorder.stream.getTracks().forEach((track) => track.stop());
      }
      mediaRecorderRef.current = null;
      flashChunksRef.current = [];
    };
  }, [clearTimers, cleanupStreamingAudio, closeStreamSocket]);

  const subscribeCaptureFinished = useCallback(
    (
      listener: (
        reason: "user_stop" | "silence" | "no_speech" | "error" | "max",
      ) => void,
    ): (() => void) => {
      captureFinishedListenersRef.current.add(listener);
      return () => {
        captureFinishedListenersRef.current.delete(listener);
      };
    },
    [],
  );

  const subscribeGenerationSuccess = useCallback(
    (
      listener: (payload: {
        eventSeq: number;
        captureId: string;
        relationshipId: string;
        isFromHistoricalPage: boolean;
      }) => void,
    ): (() => void) => {
      generationSuccessListenersRef.current.add(listener);
      return () => {
        generationSuccessListenersRef.current.delete(listener);
      };
    },
    [],
  );

  return {
    status,
    mode,
    setMode,
    elapsedSec,
    toggle,
    start,
    finishRecording,
    subscribeCaptureFinished,
    subscribeGenerationSuccess,
    // Capture-only busy signal. Backed by a real React state so it can
    // re-trigger effects that gate Subject Lift. Decoupled from the
    // generation pipeline (which lives in processingJobsRef).
    captureBusy: captureBusyState,
    // Public counter of background generation jobs. Allows the UI to
    // render a "processing N" indicator without colliding with the
    // microphone-capture lifecycle.
    processingJobCount: processingJobCountState,
    // Stable jobId of the active capture (or "" while idle). Subject
    // Lift stores this alongside its historical-pending context so an
    // async generation result cannot be credited against the wrong
    // captureId.
    getCurrentCaptureId,
  };
}

function calculateRms(samples: Float32Array): number {
  if (!samples.length) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / samples.length);
}

function downsampleToPcm16(
  input: Float32Array,
  inputRate: number,
  outputRate: number,
): Int16Array {
  if (!input.length) return new Int16Array(0);
  const ratio = inputRate / outputRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Int16Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    const count = Math.max(1, end - start);
    for (let cursor = start; cursor < end; cursor += 1) sum += input[cursor];
    const sample = clamp(sum / count, -1, 1);
    output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

function concatInt16(left: Int16Array, right: Int16Array): Int16Array {
  const result = new Int16Array(left.length + right.length);
  result.set(left);
  result.set(right, left.length);
  return result;
}

function percentile(values: number[], ratio: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * ratio) - 1)];
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function encodePcmWav(chunks: Int16Array[], sampleRate: number): Blob {
  const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + sampleCount * 2);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + sampleCount * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, sampleCount * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (const sample of chunk) {
      view.setInt16(offset, sample, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1)
    view.setUint8(offset + index, value.charCodeAt(index));
}
