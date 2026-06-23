import type { DemoId } from "./types/scene";

export const DEMO_PRESETS: { id: DemoId; label: string }[] = [
  { id: "calm", label: "平静" },
  { id: "longing", label: "想念" },
  { id: "fatigue", label: "疲倦" },
  { id: "await_response", label: "等待回应" },
];

export const WS_PATH = "/ws/scene";
export const TOAST_DEFAULT_MS = 3500;
