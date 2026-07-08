import type { Scene, SceneEvent, TouchPayload, DemoId } from "./types/scene";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
  }
  return (await res.json()) as T;
}

function absoluteApiUrl(path: string): string {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${API_BASE}${path}`;
}

export async function getScene(): Promise<Scene> {
  return json<Scene>("/scene");
}

export async function switchDemo(demoId: DemoId): Promise<Scene> {
  return json<Scene>(`/demo/${demoId}`, { method: "POST" });
}

export async function postTouch(payload: TouchPayload): Promise<SceneEvent> {
  return json<SceneEvent>("/touch", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type UploadAudioResult = {
  transcript: string;
  imageUrl?: string;
  raw?: unknown;
};

type AgentRunAudioResponse = {
  runId: string;
  status: string;
  result?: {
    languageEmotion?: {
      transcript?: string;
    };
    imageGeneration?: {
      wallpaperUrl?: string;
    };
  };
};

export async function uploadAudio(blob: Blob): Promise<UploadAudioResult> {
  const form = new FormData();
  form.append("audio", blob, `voice-${Date.now()}.wav`);
  const res = await fetch(`${API_BASE}/agent-runs/audio`, { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Agent run HTTP ${res.status}: ${detail || res.statusText}`);
  }
  const payload = (await res.json()) as AgentRunAudioResponse;
  const result: UploadAudioResult = {
    transcript: payload.result?.languageEmotion?.transcript ?? "",
    imageUrl: payload.result?.imageGeneration?.wallpaperUrl ?? "",
    raw: payload,
  };
  return {
    ...result,
    imageUrl: result.imageUrl ? absoluteApiUrl(result.imageUrl) : "",
  };
}

export async function getLogs(): Promise<unknown[]> {
  return json<unknown[]>("/logs");
}
