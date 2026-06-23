"use client";

import { create } from "zustand";
import { startAmbient, stopAmbient } from "@/lib/audio";
import type { Scene, SceneEvent } from "@/lib/types/scene";

export type VoiceMessage = {
  id: string;
  type: "voice_message";
  from: "child" | "elder";
  audioUrl: string;
  text: string;
  durationSec: number;
  timestamp: number;
};

export type TextMessage = {
  id: string;
  type: "text_message";
  from: "child" | "elder";
  text: string;
  timestamp: number;
};

export type Message = VoiceMessage | TextMessage;
export type NewMessage = Omit<VoiceMessage, "id" | "timestamp"> | Omit<TextMessage, "id" | "timestamp">;

export type MessagesByDay = {
  前天: Message[];
  昨天: Message[];
  今天: Message[];
};

export type UiMode = "wallpaper" | "white";

const DAY_LABELS: (keyof MessagesByDay)[] = ["前天", "昨天", "今天"];
const STORAGE_KEY = "scene_chat_history";

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function blank(): MessagesByDay {
  return { 前天: [], 昨天: [], 今天: [] };
}

function mockData(): MessagesByDay {
  const now = Date.now();
  return {
    前天: [
      { id: uid(), type: "text_message", from: "child", text: "奶奶，昨天晚上梦到你了，梦见我们一起去公园玩。", timestamp: now - 3600000 * 28 },
      { id: uid(), type: "text_message", from: "elder", text: "宝贝，梦里的公园是不是很漂亮呀？奶奶也想你。", timestamp: now - 3600000 * 27 },
      { id: uid(), type: "text_message", from: "child", text: "是的！奶奶你要注意身体，天冷了要多穿衣服。", timestamp: now - 3600000 * 26 },
    ],
    昨天: [
      { id: uid(), type: "text_message", from: "child", text: "奶奶，今天妈妈做了红烧肉，特别香！我给你留了一块。", timestamp: now - 3600000 * 20 },
      { id: uid(), type: "text_message", from: "elder", text: "哎呀，我家宝贝真乖，记得给奶奶留好吃的。", timestamp: now - 3600000 * 19 },
      { id: uid(), type: "text_message", from: "child", text: "下周我就回去看奶奶啦，带你吃大餐！", timestamp: now - 3600000 * 18 },
      { id: uid(), type: "text_message", from: "elder", text: "太好了，奶奶每天都在数着日子等你回来。", timestamp: now - 3600000 * 17 },
    ],
    今天: [
      { id: uid(), type: "text_message", from: "child", text: "奶奶，今天天气真好呀，你那边怎么样？", timestamp: now - 3600000 * 3 },
      { id: uid(), type: "text_message", from: "elder", text: "宝贝乖，奶奶这边也挺好的，就是有点想你。", timestamp: now - 3600000 * 2.5 },
      { id: uid(), type: "text_message", from: "child", text: "我下周就回来看你啦！给你带好吃的。", timestamp: now - 3600000 * 2 },
      { id: uid(), type: "text_message", from: "elder", text: "太好了，奶奶等着你，记得多穿点衣服。", timestamp: now - 3600000 * 1.5 },
      { id: uid(), type: "text_message", from: "child", text: "知道啦奶奶，那我先去上课了，晚上再给你打电话。", timestamp: now - 3600000 },
      { id: uid(), type: "text_message", from: "elder", text: "好，去吧，好好学习，奶奶爱你。", timestamp: now - 3600000 * 0.5 },
    ],
  };
}

function loadMessages(): MessagesByDay {
  if (typeof window === "undefined") return blank();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as MessagesByDay;
  } catch {
    /* ignore */
  }
  return mockData();
}

function persist(msgs: MessagesByDay) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(msgs));
  } catch {
    /* storage full */
  }
}

type State = {
  _ambientSrc: string | null;
  _toastTimer: ReturnType<typeof setTimeout> | null;

  scene: Scene | null;
  setScene: (s: Scene) => void;
  applyEvent: (ev: SceneEvent) => void;

  toast: string | null;
  setToast: (msg: string | null) => void;

  uiMode: UiMode;
  toggleUiMode: () => void;

  currentDayIndex: number;
  messagesByDay: MessagesByDay;
  shiftDay: (dir: -1 | 1) => void;

  generatedWallpaperUrl: string;
  setGeneratedWallpaperUrl: (url: string) => void;

  addMessage: (msg: NewMessage) => void;
  clearMessages: () => void;
};

export const useSceneStore = create<State>((set, get) => ({
  _ambientSrc: null,
  _toastTimer: null,

  scene: null,

  setScene(scene: Scene) {
    const prev = get()._ambientSrc;
    const next = scene.ambient ?? null;
    if (next && next !== prev) startAmbient(next);
    else if (!next && prev) stopAmbient(prev);
    set({ scene, _ambientSrc: next });
  },

  applyEvent(ev: SceneEvent) {
    const s = get();
    if (ev.scene) s.setScene(ev.scene);
    if (ev.toast) s.setToast(ev.toast);
  },

  toast: null,

  setToast(msg: string | null) {
    const prev = get()._toastTimer;
    if (prev) clearTimeout(prev);
    const id = msg ? setTimeout(() => set({ toast: null }), 3500) : null;
    set({ toast: msg, _toastTimer: id });
  },

  uiMode: "wallpaper",
  toggleUiMode() {
    set((s) => ({ uiMode: s.uiMode === "wallpaper" ? "white" : "wallpaper" }));
  },

  currentDayIndex: 2,
  messagesByDay: loadMessages(),

  shiftDay(dir: -1 | 1) {
    const next = get().currentDayIndex + dir;
    if (next < 0 || next > 2) return;
    set({ currentDayIndex: next });
  },

  generatedWallpaperUrl: "",
  setGeneratedWallpaperUrl(url: string) {
    set({ generatedWallpaperUrl: url });
  },

  addMessage(partial) {
    const msg = {
      ...partial,
      id: uid(),
      timestamp: Date.now(),
    } as Message;
    const label = DAY_LABELS[get().currentDayIndex];
    const updated = {
      ...get().messagesByDay,
      [label]: [...get().messagesByDay[label], msg],
    };
    persist(updated);
    set({ messagesByDay: updated });
  },

  clearMessages() {
    const blank$ = blank();
    persist(blank$);
    set({ messagesByDay: blank$ });
  },
}));
