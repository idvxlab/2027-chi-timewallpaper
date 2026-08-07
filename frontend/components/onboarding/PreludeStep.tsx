"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSceneStore } from "@/lib/hooks/useSceneStore";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { subscribeToWallpaperEvents } from "@/lib/wallpaperEvents";
import {
  generateFirstVoiceWallpapers,
  getCurrentWallpaper,
  updateSessionProgress,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { STATIC_BASE_SCENE_PATH } from "@/lib/api";

// The first voice runs through understanding, design, and dual-view painting.
type RecordingState =
  | "idle"
  | "recording"
  | "transcribing"
  | "editing"
  | "done"
  | "error";

// Kept for forward compatibility with future steps that may want to
// derive phase-driven atmosphere envelope changes (e.g. "listening"
// vs "settling"). Step 2 only sets it to "idle".
type Phase = "idle" | "listening" | "ready";

// Static fallback used only when the store has no URL yet (e.g. if
// PhotoUploadScreen did not call the generation endpoint for any reason).
// For the 2027 backend, this is the canonical base-scene path served
// from the backend's static handler (Nginx-proxied in production).
const WALLPAPER_FALLBACK = STATIC_BASE_SCENE_PATH;

// Subtle low-opacity dark wash layered over the photo backdrop, lifted
// directly from the previous implementation so the breathing ring stays
// readable on any near-white sky region of the generated wallpaper.
const READABILITY_VEIL = "rgba(0, 0, 0, 0.12)";

// Hard cap on a single recording to avoid runaway microphone sessions
// when the user forgets to tap the circle a second time.
const MAX_RECORDING_MS = 10_000;

// Minimum acceptable recording length. Anything shorter is a misclick.
const MIN_RECORDING_MS = 1200;

// Below this size the encoded audio is too short to provide useful evidence.
const MIN_BLOB_BYTES = 2000;

// MIME-type preference order for MediaRecorder. Some browsers (Safari)
// don't support webm; we fall back to whatever the UA reports it can
// record with.
const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

function pickRecorderMimeType(): string | undefined {
  if (
    typeof window === "undefined" ||
    typeof window.MediaRecorder === "undefined"
  ) {
    return undefined;
  }
  for (const candidate of PREFERRED_MIME_TYPES) {
    if (window.MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return undefined;
}

// ── SVG Icons ───────────────────────────────────────────────────────────────

function MicIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 3.5a3 3 0 0 0-3 4v5a3 3 0 0 0 6 0v-5a3 3 0 0 0-3-3Z" />
      <path d="M6.5 10.5a5.5 5.5 0 0 0 11 0" />
      <path d="M12 16v3.5" />
      <path d="M9 20h6" />
    </svg>
  );
}

// ── Voice Portal — the redesigned recording entry ──────────────────────────────

type VoicePortalState = "idle" | "recording" | "processing";

function VoicePortal({
  state,
}: {
  state: VoicePortalState;
}) {
  const { t } = useI18n();
  const isIdle = state === "idle";
  const isRecording = state === "recording";
  const isProcessing = state === "processing";

  return (
    <div className="voice-portal-root relative flex flex-col items-center justify-center">
      {/* Outer breathing glow — visible in all states */}
      <div
        className={[
          "voice-outer absolute rounded-full",
          isRecording ? "animate-voice-outer-active" : "animate-voice-outer-idle",
        ].join(" ")}
        style={{
          width: 144,
          height: 144,
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
        }}
      />

      {/* Main circle button */}
      <div
        className={[
          "voice-main relative flex items-center justify-center rounded-full",
          "transition-all duration-300",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-purple-400 focus-visible:ring-offset-2 focus-visible:ring-offset-white/40",
          isRecording
            ? "h-[96px] w-[96px] bg-white/65 border border-white/80 shadow-[0_16px_40px_rgba(168,85,247,0.32)]"
            : isProcessing
              ? "h-[96px] w-[96px] bg-white/55 border border-white/70 shadow-[0_12px_32px_rgba(168,85,247,0.22)]"
              : "h-[96px] w-[96px] bg-white/50 border border-white/70 shadow-[0_12px_35px_rgba(120,80,160,0.22)]",
        ].join(" ")}
      >
        {/* Inner icon area */}
        <div className="relative flex items-center justify-center">
          {isRecording ? (
            /* Recording state: wave-dot animation */
            <div className="voice-wave-dots flex items-end gap-1.5">
              <span className="voice-dot h-2 w-2 rounded-full bg-purple-400 animate-voice-wave-dot" />
              <span className="voice-dot h-3.5 w-2 rounded-full bg-orange-400 animate-voice-wave-dot" style={{ animationDelay: "120ms" }} />
              <span className="voice-dot h-2 w-2 rounded-full bg-purple-400 animate-voice-wave-dot" style={{ animationDelay: "240ms" }} />
            </div>
          ) : isProcessing ? (
            /* Processing state: loading dot */
            <div className="voice-loading-dots flex items-end gap-1.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="voice-dot h-2.5 w-2.5 rounded-full"
                  style={{
                    background: i === 1 ? "#C179F2" : "#d8b4fe",
                    animation: `voice-loading-dot 1.2s ease-in-out ${i * 180}ms infinite`,
                  }}
                />
              ))}
            </div>
          ) : (
            /* Idle state: microphone icon */
            <MicIcon className="voice-mic-icon h-8 w-8 text-purple-500" />
          )}
        </div>
      </div>

      {/* Label below the button */}
      <div
        className={[
          "voice-label mt-3 text-[13px] font-semibold tracking-wide transition-all duration-300",
          isRecording ? "text-purple-500/90" : isProcessing ? "text-slate-500" : "text-slate-600/80",
        ].join(" ")}
      >
        {isRecording
          ? t("prelude.listening")
          : isProcessing
            ? t("prelude.creating")
            : t("prelude.tapToSpeak")}
      </div>

      {/* Idle hint — tiny subtitle */}
      {isIdle && (
        <div className="voice-hint mt-1 text-[11px] text-slate-400/70">
          {t("prelude.recordHint")}
        </div>
      )}
    </div>
  );
}

// ── AmbientField ────────────────────────────────────────────────────────────

function AmbientField({
  phase,
  wallpaperUrl,
}: {
  phase: Phase;
  wallpaperUrl: string;
}) {
  const url = wallpaperUrl || WALLPAPER_FALLBACK;
  return (
    <div
      aria-hidden
      data-atmosphere={phase}
      className="pre-atmosphere pointer-events-none absolute inset-0 overflow-hidden"
      style={{
        opacity: "var(--pre-ambient-opacity)",
        filter:
          "saturate(var(--pre-ambient-saturation)) brightness(var(--pre-ambient-brightness))",
        backgroundImage: `url(${url})`,
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
      }}
    >
      <div
        aria-hidden
        className="absolute inset-0"
        style={{ backgroundColor: READABILITY_VEIL }}
      />
    </div>
  );
}

// ── PreludeStep ─────────────────────────────────────────────────────────────

export function PreludeStep() {
  const { t } = useI18n();
  // Wallpaper URL for display (AmbientField). Updated by PhotoUploadScreen.
  const generatedWallpaperUrl = useSceneStore((s) => s.generatedWallpaperUrl);
  // Updated after the backend returns this viewer's personalized wallpaper.
  const setGeneratedWallpaperUrl = useSceneStore(
    (s) => s.setGeneratedWallpaperUrl,
  );

  // The role selects one of the two views generated from the same first voice.
  const role = useOnboardingStore((s) => s.role);

  // Step 3: on completion jump to the final WallpaperStage.
  const setStep = useOnboardingStore((s) => s.setStep);

  const [phase] = useState<Phase>("idle");
  const [recState, setRecState] = useState<RecordingState>("idle");
  const [transcript, setTranscript] = useState<string>("");

  // Live refs so async callbacks always see the latest state without
  // re-binding handlers on every render.
  const recStateRef = useRef<RecordingState>("idle");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const maxDurationTimerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number>(0);

  useEffect(() => {
    let active = true;
    let requestInFlight = false;

    const refreshDelayedView = async () => {
      if (
        !active ||
        requestInFlight ||
        !["idle", "error"].includes(recStateRef.current)
      ) {
        return;
      }
      requestInFlight = true;
      try {
        const current = await getCurrentWallpaper();
        const hasPersonalizedView =
          current.stage === "wallpaper_active" &&
          Boolean(current.wallpaperUrl) &&
          current.wallpaperUrl !== current.baseSceneUrl;
        if (!active || !hasPersonalizedView) return;

        setGeneratedWallpaperUrl(current.wallpaperUrl);
        useSceneStore.getState().setWallpaperVersion(current.version);
        useSceneStore.getState().setInitialInsertStatus("ready");
        setStep("wallpaper");
      } catch (error) {
        console.debug("[PreludeStep] delayed view is not ready", error);
      } finally {
        requestInFlight = false;
      }
    };

    void refreshDelayedView();
    const unsubscribe = subscribeToWallpaperEvents(
      () => void refreshDelayedView(),
    );
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void refreshDelayedView();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      unsubscribe();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [setGeneratedWallpaperUrl, setStep]);

  const generateFirstVoice = useCallback(
    async (audioBlob: Blob) => {
      const viewerRole: "child" | "elder" =
        role === "elder" ? "elder" : "child";
      const sessionId = useSceneStore.getState().generationSessionId;

      useSceneStore.getState().setInitialInsertStatus("generating");
      setRecState("editing");
      recStateRef.current = "editing";
      mediaRecorderRef.current = null;
      chunksRef.current = [];

      try {
        const response = await generateFirstVoiceWallpapers(audioBlob);
        if (useSceneStore.getState().generationSessionId !== sessionId) {
          console.warn("[PreludeStep] ignored stale first-voice result");
          useSceneStore.getState().setInitialInsertStatus("failed");
          setRecState("error");
          recStateRef.current = "error";
          return;
        }

        const result = response.result;
        const views = result.wallpaperViews;
        const wallpaperUrl =
          viewerRole === "elder"
            ? views?.elderViewUrl || ""
            : views?.childViewUrl || "";
        if (!wallpaperUrl) {
          throw new Error(`Missing ${viewerRole} wallpaper view`);
        }

        setTranscript(result.languageEmotion?.transcript || "");
        setGeneratedWallpaperUrl(wallpaperUrl);
        useSceneStore.getState().setInitialInsertStatus("ready");
        try {
          await updateSessionProgress({
            onboardingStep: "wallpaper",
            wallpaperUrl,
          });
        } catch (error) {
          console.error("[PreludeStep] failed to persist session progress", error);
        }

        setRecState("done");
        recStateRef.current = "done";
        setStep("wallpaper");
      } catch (error) {
        console.error("[PreludeStep] first-voice generation failed", error);
        useSceneStore.getState().setInitialInsertStatus("failed");
        setRecState("error");
        recStateRef.current = "error";
      }
    },
    [role, setGeneratedWallpaperUrl, setStep],
  );

  useEffect(() => {
    recStateRef.current = recState;
  }, [recState]);

  const clearMaxDurationTimer = () => {
    if (maxDurationTimerRef.current !== null) {
      window.clearTimeout(maxDurationTimerRef.current);
      maxDurationTimerRef.current = null;
    }
  };

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  // Hard safety net: never leave a live mic stream or timer when the
  // component unmounts mid-recording.
  useEffect(() => {
    return () => {
      clearMaxDurationTimer();
      try {
        if (
          mediaRecorderRef.current &&
          mediaRecorderRef.current.state !== "inactive"
        ) {
          mediaRecorderRef.current.stop();
        }
      } catch {
        /* ignore */
      }
      releaseStream();
    };
  }, [releaseStream]);

  const startRecording = async () => {
    if (recStateRef.current !== "idle") return;
    if (typeof window === "undefined") return;

    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof window.MediaRecorder === "undefined"
    ) {
      console.error("[PreludeStep] browser does not support audio recording");
      setRecState("error");
      recStateRef.current = "error";
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch (err) {
      console.error("[PreludeStep] getUserMedia failed:", err);
      setRecState("error");
      recStateRef.current = "error";
      return;
    }

    const mimeType = pickRecorderMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
    } catch (err) {
      console.error("[PreludeStep] MediaRecorder ctor failed:", err);
      stream.getTracks().forEach((t) => t.stop());
      setRecState("error");
      recStateRef.current = "error";
      return;
    }

    streamRef.current = stream;
    mediaRecorderRef.current = recorder;
    chunksRef.current = [];

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        chunksRef.current.push(event.data);
      }
    };

    recorder.onerror = (event) => {
      console.error("[PreludeStep] MediaRecorder error:", event);
    };

    recorder.onstop = () => {
      // Compose the final blob from collected chunks.
      const mime = recorder.mimeType || mimeType || "audio/webm";
      const blob = new Blob(chunksRef.current, { type: mime });
      // Release the mic immediately; the multi-agent pipeline runs afterwards.
      releaseStream();
      clearMaxDurationTimer();

      console.log(
        "[PreludeStep] recorded blob size/type =",
        blob.size,
        "/",
        blob.type,
      );

      // A tiny blob cannot carry useful speech evidence. Stay here so the
      // user can retry instead of generating a wallpaper from empty audio.
      if (blob.size < MIN_BLOB_BYTES) {
        console.warn("[PreludeStep] recorded audio too small, please retry");
        setRecState("error");
        recStateRef.current = "error";
        return;
      }

      setRecState("transcribing");
      recStateRef.current = "transcribing";
      console.log("[PreludeStep] sending first voice to multi-agent pipeline");
      void generateFirstVoice(blob);
    };

    try {
      // timeslice (250 ms) guarantees that ondataavailable fires
      // periodically even on browsers that batch chunks aggressively.
      recorder.start(250);
    } catch (err) {
      console.error("[PreludeStep] MediaRecorder.start failed:", err);
      releaseStream();
      setRecState("error");
      recStateRef.current = "error";
      return;
    }

    startedAtRef.current = Date.now();
    setRecState("recording");
    recStateRef.current = "recording";
    console.log("[PreludeStep] recording started");

    // Auto-stop after MAX_RECORDING_MS so we never hold the mic open
    // indefinitely if the user forgets to tap again.
    clearMaxDurationTimer();
    maxDurationTimerRef.current = window.setTimeout(() => {
      if (
        mediaRecorderRef.current &&
        mediaRecorderRef.current.state !== "inactive"
      ) {
        console.log(
          "[PreludeStep] max recording duration reached, auto-stopping",
        );
        stopRecording("max");
      }
    }, MAX_RECORDING_MS);
  };

  const stopRecording = (reason: "manual" | "max" = "manual") => {
    if (recStateRef.current !== "recording") return;
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;

    const elapsedMs = Date.now() - startedAtRef.current;
    if (elapsedMs < MIN_RECORDING_MS) {
      // Too short to bother the ASR provider; ignore the stop request
      // so the user can keep recording. The MAX_RECORDING_MS timer is
      // still armed as a hard safety net.
      console.log("[PreludeStep] recording too short, ignoring stop request");
      return;
    }

    clearMaxDurationTimer();
    console.log("[PreludeStep] recording stopped", { reason, elapsedMs });
    // Flush any buffered audio into a final chunk before stopping so
    // ondataavailable sees the very last samples.
    try {
      if (typeof recorder.requestData === "function") {
        recorder.requestData();
      }
    } catch (err) {
      console.warn("[PreludeStep] recorder.requestData failed:", err);
    }
    recorder.stop();
  };

  const onRecordCircleClicked = () => {
    if (recState === "error") {
      // Recovery path: a single tap after a failed attempt clears
      // the error and resumes from idle without firing the API again.
      setRecState("idle");
      recStateRef.current = "idle";
      return;
    }
    if (recState === "recording") {
      stopRecording("manual");
      return;
    }
    if (recState === "idle") {
      void startRecording();
      return;
    }
    // transcribing / done: ignore further taps until we land in idle
    // (transcribing flips to done|error automatically).
  };

  // Map RecordingState to VoicePortalState
  const voicePortalState: "idle" | "recording" | "processing" =
    recState === "idle" || recState === "error"
      ? "idle"
      : recState === "recording"
        ? "recording"
        : "processing";

  const isClickable = recState === "idle" || recState === "error";

  return (
    <div
      className="relative h-full w-full overflow-hidden bg-white"
      data-phase={phase}
      data-rec-state={recState}
      data-transcript={transcript}
      aria-busy={recState === "recording" || recState === "transcribing"}
    >
      <AmbientField phase={phase} wallpaperUrl={generatedWallpaperUrl} />

      <div className="relative z-10 flex h-full w-full flex-col items-center justify-center px-10">
        {/* Voice Portal — the main recording entry */}
        <div className="flex flex-col items-center justify-center">
          <button
            type="button"
            onClick={onRecordCircleClicked}
            disabled={!isClickable}
            aria-label={
              recState === "recording"
                ? t("prelude.stopRecording")
                : recState === "idle"
                  ? t("prelude.startRecording")
                  : recState === "error"
                    ? t("prelude.retryRecording")
                    : t("prelude.processing")
            }
            className={[
              "flex flex-col items-center justify-center",
              "rounded-full border-0 bg-transparent p-0",
              "transition-all duration-200",
              "focus:outline-none",
              isClickable
                ? "cursor-pointer"
                : "cursor-wait opacity-80",
            ].join(" ")}
          >
            <VoicePortal state={voicePortalState} />
          </button>
        </div>
      </div>

      <style jsx>{`
        @keyframes voice-outer-idle {
          0%,
          100% {
            transform: translate(-50%, -50%) scale(0.96);
            opacity: 0.38;
          }
          50% {
            transform: translate(-50%, -50%) scale(1.06);
            opacity: 0.62;
          }
        }

        @keyframes voice-outer-active {
          0%,
          100% {
            transform: translate(-50%, -50%) scale(0.94);
            opacity: 0.55;
          }
          50% {
            transform: translate(-50%, -50%) scale(1.10);
            opacity: 0.78;
          }
        }

        @keyframes voice-wave-dot {
          0%,
          100% {
            transform: translateY(0);
            opacity: 0.5;
          }
          50% {
            transform: translateY(-6px);
            opacity: 1;
          }
        }

        @keyframes voice-loading-dot {
          0%,
          100% {
            opacity: 0.35;
            transform: scaleY(0.7);
          }
          50% {
            opacity: 1;
            transform: scaleY(1);
          }
        }

        .animate-voice-outer-idle {
          animation: voice-outer-idle 2.6s ease-in-out infinite;
        }

        .animate-voice-outer-active {
          animation: voice-outer-active 1.8s ease-in-out infinite;
        }

        .animate-voice-wave-dot {
          animation: voice-wave-dot 1s ease-in-out infinite;
        }

        :global([data-atmosphere="idle"]) {
          --pre-ambient-opacity: 1;
          --pre-ambient-saturation: 1;
          --pre-ambient-brightness: 1;
        }
        :global([data-atmosphere="listening"]) {
          --pre-ambient-opacity: 1.35;
          --pre-ambient-saturation: 1.12;
          --pre-ambient-brightness: 1.02;
        }
        :global([data-atmosphere="ready"]) {
          --pre-ambient-opacity: 0.78;
          --pre-ambient-saturation: 0.88;
          --pre-ambient-brightness: 0.97;
        }

        :global(.pre-atmosphere) {
          transition:
            opacity 2000ms ease-out,
            filter 2000ms ease-out;
        }
      `}</style>
    </div>
  );
}
