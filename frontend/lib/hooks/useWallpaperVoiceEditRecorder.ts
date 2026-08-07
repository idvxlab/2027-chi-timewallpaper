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
    initialEndSilenceMs: 750,
    minEndSilenceMs: 650,
    maxEndSilenceMs: 1_500,
    minSpeechMs: 300,
    maxRecordingMs: 20_000,
    noSpeechTimeoutMs: 3_000,
    minRms: 0.01,
  },
  elder: {
    initialEndSilenceMs: 1_200,
    minEndSilenceMs: 650,
    maxEndSilenceMs: 1_500,
    minSpeechMs: 400,
    maxRecordingMs: 30_000,
    noSpeechTimeoutMs: 5_000,
    minRms: 0.006,
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
  const adaptiveEndSilenceRef = useRef(750);
  const timerRef = useRef<number | null>(null);
  const maxTimerRef = useRef<number | null>(null);
  const noSpeechTimerRef = useRef<number | null>(null);
  const viewerRoleRef = useRef<ViewerRole>("child");
  const isBusyRef = useRef(false);
  const requestIdRef = useRef("");
  const activeSourceRef = useRef<RecorderSource>("subject_lift");

  const clearTimers = useCallback(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    if (maxTimerRef.current !== null) window.clearTimeout(maxTimerRef.current);
    if (noSpeechTimerRef.current !== null)
      window.clearTimeout(noSpeechTimerRef.current);
    timerRef.current = null;
    maxTimerRef.current = null;
    noSpeechTimerRef.current = null;
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

  const finishStreamingRecording = useCallback(() => {
    if (endingRef.current || streamFinishedRef.current) return;
    endingRef.current = true;
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
  }, [clearTimers, cleanupStreamingAudio, closeStreamSocket, flushStreamingPacket, setStatus]);

  const finishFlashRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
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
  }, [clearTimers, processFlashRecording, setStatus]);

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
        clearTimers();
        stream.getTracks().forEach((track) => track.stop());
        mediaRecorderRef.current = null;
        flashChunksRef.current = [];
        isBusyRef.current = false;
        setStatus("idle");
      };
      recorder.start(STREAM_CHUNK_MS);
    },
    [clearTimers, setStatus],
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
            now - speechStartedAtRef.current >= profile.minSpeechMs &&
            now - lastSpeechAtRef.current >= adaptiveEndSilenceRef.current
          ) {
            finishStreamingRecording();
          }
        }
      };
      source.connect(processor);
      processor.connect(context.destination);
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

  const startRecording = useCallback(async (source: RecorderSource) => {
    if (isBusyRef.current || mediaRecorderRef.current || streamSocketRef.current)
      return;
    const scene = useSceneStore.getState();
    if (!isLatestWallpaper(scene) || !scene.generatedWallpaperUrl) {
      console.warn("[WallpaperVoiceEdit] select the latest wallpaper before recording");
      return;
    }
    const interactionMode = getWallpaperInteractionMode(scene);
    if (
      (source === "central_button" &&
        interactionMode !== "initial_voice" &&
        interactionMode !== "processing") ||
      (source === "subject_lift" && interactionMode !== "subject_lift")
    ) {
      console.warn(
        `[WallpaperVoiceEdit] ${source} rejected: mode=${interactionMode}`,
      );
      return;
    }

    try {
      const current = await getCurrentWallpaper();
      if (source === "central_button" && current.stage === "wallpaper_active") {
        console.warn(
          "[WallpaperVoiceEdit] central button not allowed after first voice is done",
        );
        return;
      }
      if (source === "subject_lift" && current.stage !== "wallpaper_active") {
        console.warn(
          "[WallpaperVoiceEdit] subject lift not allowed before first voice",
        );
        return;
      }
    } catch (error) {
      console.error(
        "[WallpaperVoiceEdit] cannot reach backend stage, recording aborted",
        error,
      );
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      console.error("[WallpaperVoiceEdit] browser does not support recording");
      return;
    }
    activeSourceRef.current = source;
    const requestedMode =
      source === "central_button" ? "flash" : useVoiceRuntimeStore.getState().mode;
    if (
      requestedMode === "flash" &&
      !window.MediaRecorder
    ) {
      console.error("[WallpaperVoiceEdit] browser does not support MediaRecorder");
      return;
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
    } catch {
      console.error("[WallpaperVoiceEdit] microphone permission denied");
      return;
    }

    const profile = VOICE_PROFILES[viewerRoleRef.current];
    startedAtRef.current = Date.now();
    requestIdRef.current = crypto.randomUUID();
    lastSpeechAtRef.current = 0;
    speechStartedAtRef.current = 0;
    observedPausesRef.current = [];
    adaptiveEndSilenceRef.current = profile.initialEndSilenceMs;
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
          "[WallpaperVoiceEdit] streaming could not start; recording with flash",
          error,
        );
        closeStreamSocket();
        activeMode = "flash";
        await startFlashRecording(stream);
      } else {
        stream.getTracks().forEach((track) => track.stop());
        isBusyRef.current = false;
        if (!streamFinishedRef.current) {
          fallbackStreamToFlash(
            error instanceof Error ? error.message : String(error),
          );
        }
        return;
      }
    }

    setStatus("recording");
    timerRef.current = window.setInterval(() => {
      setElapsedSec(Math.round((Date.now() - startedAtRef.current) / 1000));
    }, 200);
    maxTimerRef.current = window.setTimeout(() => {
      if (activeMode === "stream")
        finishStreamingRecording();
      else finishFlashRecording();
    }, profile.maxRecordingMs);
    if (activeMode === "stream") {
      noSpeechTimerRef.current = window.setTimeout(() => {
        if (!speechStartedAtRef.current) finishStreamingRecording();
      }, profile.noSpeechTimeoutMs);
    }
  }, [
    fallbackStreamToFlash,
    finishFlashRecording,
    finishStreamingRecording,
    closeStreamSocket,
    setStatus,
    setRuntimeMode,
    startFlashRecording,
    startStreamingRecording,
  ]);

  const finishRecording = useCallback(() => {
    if (streamSocketRef.current) finishStreamingRecording();
    else finishFlashRecording();
  }, [finishFlashRecording, finishStreamingRecording]);

  const toggle = useCallback(
    (viewerRole?: ViewerRole) => {
      if (viewerRole) viewerRoleRef.current = viewerRole;
      if (status === "idle") void startRecording("central_button");
      else if (status === "recording") finishRecording();
    },
    [finishRecording, startRecording, status],
  );

  const start = useCallback(
    (viewerRole: ViewerRole, source: RecorderSource = "subject_lift") => {
      viewerRoleRef.current = viewerRole;
      if (status === "idle") void startRecording(source);
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

  return {
    status,
    mode,
    setMode,
    elapsedSec,
    toggle,
    start,
    finishRecording,
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
