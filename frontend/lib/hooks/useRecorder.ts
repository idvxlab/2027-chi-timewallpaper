"use client";

import { useRef, useState } from "react";
import { uploadAudio } from "@/lib/api";
import { useSceneStore } from "./useSceneStore";

export type RecorderStatus = "idle" | "recording" | "stopping";


export function useRecorder() {
  const [status, setStatusState] = useState<RecorderStatus>("idle");
  const [elapsedMs, setElapsedMs] = useState(0);
  const statusRef = useRef<RecorderStatus>("idle");
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const recordedLengthRef = useRef(0);
  const startedAtRef = useRef(0);
  const fromRef = useRef<"child" | "elder">("child");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const addMessage = useSceneStore((s) => s.addMessage);
  const setToast = useSceneStore((s) => s.setToast);
  const setGeneratedWallpaperUrl = useSceneStore((s) => s.setGeneratedWallpaperUrl);

  function setStatus(next: RecorderStatus) {
    statusRef.current = next;
    setStatusState(next);
  }

  function cleanup() {
    if (timerRef.current) clearInterval(timerRef.current);
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    void audioContextRef.current?.close().catch(() => undefined);
    streamRef.current = null;
    audioContextRef.current = null;
    sourceRef.current = null;
    processorRef.current = null;
    chunksRef.current = [];
    recordedLengthRef.current = 0;
  }

  async function finishRecording() {
    const durationMs = Date.now() - startedAtRef.current;
    const sampleRate = audioContextRef.current?.sampleRate ?? 44100;
    const wavBlob = encodeWav(chunksRef.current, recordedLengthRef.current, sampleRate);
    const audioUrl = URL.createObjectURL(wavBlob);
    const durationSec = Math.max(1, Math.round(durationMs / 1000));

    cleanup();

    if (durationMs < 800 || wavBlob.size < 4000) {
      setToast("录音太短，再试一次");
      setStatus("idle");
      setElapsedMs(0);
      return;
    }

    try {
      setToast("正在识别语音并生成壁纸…");
      const result = await uploadAudio(wavBlob);
      const transcript = result.transcript || "语音已收到";

      addMessage({
        type: "voice_message",
        from: fromRef.current,
        audioUrl,
        text: transcript,
        durationSec,
      });

      if (result.imageUrl) {
        setGeneratedWallpaperUrl(result.imageUrl);
        setToast("新壁纸已生成");
      } else {
        setToast("语音已识别，当前使用默认壁纸");
      }
    } catch (error) {
      setToast(error instanceof Error ? error.message : "语音处理失败");
    } finally {
      setStatus("idle");
      setElapsedMs(0);
    }
  }

  async function start(from: "child" | "elder") {
    if (statusRef.current !== "idle") return;

    const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
    if (!navigator.mediaDevices?.getUserMedia || !AudioContextClass) {
      setToast("当前浏览器不支持录音");
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
    } catch {
      setToast("请允许麦克风权限");
      return;
    }

    const audioContext = new AudioContextClass() as AudioContext;
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);

    chunksRef.current = [];
    recordedLengthRef.current = 0;
    fromRef.current = from;
    streamRef.current = stream;
    audioContextRef.current = audioContext;
    sourceRef.current = source;
    processorRef.current = processor;

    processor.onaudioprocess = (event) => {
      if (statusRef.current !== "recording") return;
      const input = event.inputBuffer.getChannelData(0);
      const copy = new Float32Array(input.length);
      copy.set(input);
      chunksRef.current.push(copy);
      recordedLengthRef.current += copy.length;
    };

    source.connect(processor);
    processor.connect(audioContext.destination);

    startedAtRef.current = Date.now();
    setStatus("recording");
    setElapsedMs(0);
    setToast(null);

    timerRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startedAtRef.current);
    }, 100);

  }

  function stop() {
    if (statusRef.current !== "recording") return;
    setStatus("stopping");
    void finishRecording();
  }

  function toggle(from: "child" | "elder") {
    if (statusRef.current === "idle") {
      void start(from);
      return;
    }
    if (statusRef.current === "recording") {
      stop();
    }
  }

  const elapsedSec = Math.round(elapsedMs / 1000);

  return { status, start, stop, toggle, elapsedSec };
}

function encodeWav(chunks: Float32Array[], length: number, sampleRate: number): Blob {
  const samples = mergeFloat32(chunks, length);
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }

  return new Blob([view], { type: "audio/wav" });
}

function mergeFloat32(chunks: Float32Array[], length: number): Float32Array {
  const result = new Float32Array(length);
  let offset = 0;
  chunks.forEach((chunk) => {
    result.set(chunk, offset);
    offset += chunk.length;
  });
  return result;
}

function writeString(view: DataView, offset: number, value: string) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}
