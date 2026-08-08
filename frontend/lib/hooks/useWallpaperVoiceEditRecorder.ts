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
import { dictionaries } from "@/lib/i18n";

export type WallpaperVoiceEditStatus =
  | "idle"
  | "recording"
  | "transcribing"
  | "editing";

export type ViewerRole = "child" | "elder";
export type VoiceAsrMode = "flash" | "stream";
export type RecorderSource = "central_button" | "subject_lift";

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
  const isBusyRef = useRef(false);
  const requestIdRef = useRef("");
  const activeSourceRef = useRef<RecorderSource>("subject_lift");

  // External listeners (e.g. Subject Lift) that want to know the exact
  // moment real audio capture stops. We fire on user stop, silence auto
  // stop, no-speech timeout, max-duration timeout, recorder onerror, and
  // flash fallback. The listeners run BEFORE the asynchronous ASR /
  // voice edit / image generation pipeline, so consumers can release
  // any visual state that should NOT persist across the full task.
  const captureFinishedListenersRef = useRef<
    Set<(reason: "user_stop" | "silence" | "no_speech" | "error" | "max") => void>
  >(new Set());

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
    (response: FirstVoiceAgentRunResult, _durationMs: number): boolean => {
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
      return true;
    },
    [setGeneratedWallpaperUrl, setWallpaperInteractions],
  );

  const processFlashRecording = useCallback(
    async (blob: Blob, durationMs: number) => {
      const profile = VOICE_PROFILES[viewerRoleRef.current];
      if (durationMs < profile.minSpeechMs || blob.size < MIN_BLOB_BYTES) {
        console.warn("[WallpaperVoiceEdit] recording too short, please retry");
        isBusyRef.current = false;
        setStatus("idle");
        return;
      }
      isBusyRef.current = true;
      setStatus("editing");
      let queued = false;
      try {
        const source = activeSourceRef.current;
        if (source === "central_button") {
          setInitialInsertStatus("generating");
          const response = await generateFirstVoiceWallpapers(blob);
          const completed = applyAgentResult(response, durationMs);
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
          queued = !applyAgentResult(response, durationMs);
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
        isBusyRef.current = false;
        if (!queued) setStatus("idle");
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
    (reason: string) => {
      if (streamAsrFinalRef.current || streamFinishedRef.current) return;
      streamFinishedRef.current = true;
      const durationMs = Date.now() - startedAtRef.current;
      const wav = encodePcmWav(allPcmRef.current, STREAM_SAMPLE_RATE);
      console.warn(`[WallpaperVoiceEdit] streaming unavailable; using flash: ${reason}`);
      clearTimers();
      cleanupStreamingAudio();
      closeStreamSocket();
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
    (reason: "user_stop" | "silence" | "no_speech" | "max" | "error" = "user_stop") => {
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
        closeStreamSocket();
        isBusyRef.current = false;
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
    (reason: "user_stop" | "silence" | "no_speech" | "max" | "error" = "user_stop") => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state === "inactive") {
        // Already inactive: still notify so any visual state can collapse.
        emitCaptureFinished(reason);
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
      const durationMs = Date.now() - startedAtRef.current;
      setElapsedSec(0);
      recorder.onstop = () => {
        recorder.stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(flashChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        flashChunksRef.current = [];
        mediaRecorderRef.current = null;
        void processFlashRecording(blob, durationMs);
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
        isBusyRef.current = false;
        setStatus("idle");
      };
      recorder.start(STREAM_CHUNK_MS);
    },
    [clearTimers, emitCaptureFinished, setStatus],
  );

  const startStreamingRecording = useCallback(
    async (stream: MediaStream) => {
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
              queued = !applyAgentResult(message.payload, durationMs);
            } finally {
              isBusyRef.current = false;
              closeStreamSocket();
              if (!queued) setStatus("idle");
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
              fallbackStreamToFlash(detail);
            } else if (!streamAsrFinalRef.current && allPcmRef.current.length === 0) {
              closeStreamSocket();
            } else {
              console.error(`[WallpaperVoiceEdit] ${detail}`);
              isBusyRef.current = false;
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
            fallbackStreamToFlash("streaming connection closed");
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
    async (source: RecorderSource): Promise<boolean> => {
      console.log("[VOICE] start_requested", { source });
      if (isBusyRef.current || mediaRecorderRef.current || streamSocketRef.current) {
        console.log("[VOICE] start_rejected: already busy or running");
        return false;
      }
      const scene = useSceneStore.getState();
      if (!isLatestWallpaper(scene) || !scene.generatedWallpaperUrl) {
        console.warn(
          "[VOICE] start_rejected: select the latest wallpaper before recording",
        );
        return false;
      }
      const interactionMode = getWallpaperInteractionMode(scene);
      if (
        (source === "central_button" &&
          interactionMode !== "initial_voice" &&
          interactionMode !== "processing") ||
        (source === "subject_lift" && interactionMode !== "subject_lift")
      ) {
        console.warn(
          `[VOICE] start_rejected: ${source} mode=${interactionMode}`,
        );
        return false;
      }

      try {
        const current = await getCurrentWallpaper();
        if (source === "central_button" && current.stage === "wallpaper_active") {
          console.warn(
            "[VOICE] start_rejected: central button not allowed after first voice is done",
          );
          return false;
        }
        if (source === "subject_lift" && current.stage !== "wallpaper_active") {
          console.warn(
            "[VOICE] start_rejected: subject lift not allowed before first voice",
          );
          return false;
        }
      } catch (error) {
        console.error(
          "[VOICE] cannot reach backend stage, recording aborted",
          error,
        );
        return false;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        console.error("[VOICE] browser does not support recording");
        return false;
      }
      activeSourceRef.current = source;
      const requestedMode =
        source === "central_button"
          ? "flash"
          : useVoiceRuntimeStore.getState().mode;
      if (requestedMode === "flash" && !window.MediaRecorder) {
        console.error("[VOICE] browser does not support MediaRecorder");
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
      isBusyRef.current = true;
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
          isBusyRef.current = false;
          if (!streamFinishedRef.current) {
            fallbackStreamToFlash(
              error instanceof Error ? error.message : String(error),
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
      maxTimerRef.current = window.setTimeout(() => {
        if (activeMode === "stream") finishStreamingRecording("max");
        else finishFlashRecording("max");
      }, profile.maxRecordingMs);
      if (activeMode === "stream") {
        noSpeechTimerRef.current = window.setTimeout(() => {
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
        const ok = await startRecording("central_button");
        console.log("[VOICE] toggle result", { ok });
      } else if (status === "recording") {
        console.log("[VOICE] stop_requested reason=toggle");
        finishRecording();
      }
    },
    [finishRecording, startRecording, status],
  );

  const start = useCallback(
    (
      viewerRole: ViewerRole,
      source: RecorderSource = "subject_lift",
    ): Promise<boolean> => {
      viewerRoleRef.current = viewerRole;
      if (status === "idle") return startRecording(source);
      // Not idle: already running or busy — caller should not transition.
      return Promise.resolve(false);
    },
    [startRecording, status],
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

  return {
    status,
    mode,
    setMode,
    elapsedSec,
    toggle,
    start,
    finishRecording,
    subscribeCaptureFinished,
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
