"use client";

import { create } from "zustand";
import { getScene } from "@/lib/api";
import { WS_PATH } from "@/lib/constants";
import { playCue, startAmbient, stopAmbient, ensureUnlocked } from "@/lib/audio";
import type { Scene, SceneEvent } from "@/lib/types/scene";

type State = {
  scene: Scene | null;
  toast: string | null;
  _ws: WebSocket | null;
  _toastTimer: number | null;
  _ambientSrc: string | null;
  connect: () => void;
  applyEvent: (ev: SceneEvent) => void;
  setScene: (s: Scene) => void;
  setToast: (msg: string | null) => void;
};

export const useSceneStore = create<State>((set, get) => ({
  scene: null,
  toast: null,
  _ws: null,
  _toastTimer: null,
  _ambientSrc: null,

  setToast(msg) {
    if (get()._toastTimer) window.clearTimeout(get()._toastTimer!);
    const id = window.setTimeout(() => set({ toast: null }), 3500);
    set({ toast: msg, _toastTimer: id });
  },

  setScene(scene) {
    const prev = get()._ambientSrc;
    const next = scene.ambient ?? null;
    if (next && next !== prev) {
      startAmbient(next);
    } else if (!next && prev) {
      stopAmbient(prev);
    }
    set({ scene, _ambientSrc: next });
  },

  applyEvent(ev) {
    if (ev.scene) get().setScene(ev.scene);
    if (ev.toast) get().setToast(ev.toast);
    if (ev.cue) playCue(ev.cue);
    if (ev.ambientStart) startAmbient(ev.ambientStart);
    if (ev.ambientStop) stopAmbient(ev.ambientStop);
  },

  async connect() {
    if (get()._ws) return;
    await ensureUnlocked();
    const scene = await getScene().catch(() => null);
    if (scene) get().setScene(scene);

    const wsBase = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000";
    const ws = new WebSocket(`${wsBase}${WS_PATH}`);
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as SceneEvent;
        get().applyEvent(ev);
      } catch {
        /* 忽略坏包 */
      }
    };
    ws.onclose = () => set({ _ws: null });
    set({ _ws: ws });
  },
}));

export function useSceneSocket() {
  const connect = useSceneStore((s) => s.connect);
  if (typeof window !== "undefined") {
    // 首次挂载时建立连接(zustand 内部会幂等)
    connect();
  }
}
