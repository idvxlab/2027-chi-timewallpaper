export type DemoId = "calm" | "longing" | "fatigue" | "await_response";

export type Hotspot = {
  id: string;
  label: string;
  x: number; // 0~1
  y: number; // 0~1
  r: number; // 半径相对值
};

export type Scene = {
  demoId: DemoId;
  glow: boolean;
  glowOpacity?: number;
  hotspots: Hotspot[];
  ambient?: string;
};

export type TouchPayload = {
  hotspotId: string;
  demoId?: DemoId;
};

export type SceneEvent = {
  scene?: Scene;
  toast?: string;
  cue?: string;
  ambientStart?: string;
  ambientStop?: string;
};
