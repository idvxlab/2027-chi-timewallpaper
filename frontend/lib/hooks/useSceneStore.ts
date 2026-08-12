"use client";

import { create } from "zustand";

// Imported here because the WallpaperStage system is the only consumer
// of these types; keeping them adjacent to the scene store avoids
// leaking them into the onboarding or pipeline surfaces.
import type { WallpaperEnvelope } from "@/lib/wallpaperEnv";
import type { RelationshipSummary } from "@/lib/api";

export type VoiceMessage = {
  id: string;
  type: "voice_message";
  from: "child" | "elder";
  audioUrl: string;
  text: string;
  durationSec: number;
  timestamp: number;
  suggestedReply?: string;
  suggestedReplies?: string[];
  relationshipSummary?: RelationshipSummary;
};

export type TextMessage = {
  id: string;
  type: "text_message";
  from: "child" | "elder";
  text: string;
  timestamp: number;
  suggestedReply?: string;
  suggestedReplies?: string[];
  relationshipSummary?: RelationshipSummary;
};

export type Message = VoiceMessage | TextMessage;
export type NewMessage =
  | Omit<VoiceMessage, "id" | "timestamp">
  | Omit<TextMessage, "id" | "timestamp">;

/**
 * 7-day rolling window for wallpaper history.
 * index 0 = 6 days ago, index 6 = today.
 */
export const DAY_LABELS = [
  "6天前",
  "5天前",
  "4天前",
  "3天前",
  "前天",
  "昨天",
  "今天",
] as const;

export type DayLabel = (typeof DAY_LABELS)[number];
export const TODAY_INDEX = DAY_LABELS.length - 1; // 6

export type MessagesByDay = Record<DayLabel, Message[]>;

export type WallpaperItem = {
  revisionId: string;
  eventSeq: number;
  imageUrl: string;
  createdAt: string;
  transcript?: string;
  reply?: string;
  suggestedReplies?: string[];
  relationshipSummary?: RelationshipSummary;
  speakerRole?: "child" | "elder" | null;
  isDemo?: boolean;
};

export type WallpaperInteractionItem = {
  eventId: string;
  eventSeq: number;
  status: string;
  transcript: string;
  reply: string;
  suggestedReplies?: string[];
  relationshipSummary?: RelationshipSummary;
  speakerRole: "child" | "elder";
  createdAt: string;
};

export type WallpapersByDay = Record<DayLabel, WallpaperItem[]>;

export type UiMode = "wallpaper" | "white";

// "Perceptual attention shift system": which subject the user's gaze
// is centered on inside the shared memory scene. "balanced" is the
// default relaxed state — neither subject is privileged. "elder" and
// "child" bias the framing toward one of the two figures.
export type FocusMode = "balanced" | "elder" | "child";

const STORAGE_KEY = "wallpaper_chat_history";

/** Returns a "M/D" string for a given day index.
 *  0 = 6 days ago
 *  ...
 *  TODAY_INDEX (6) = today */
export function formatDayLabel(dayIndex: number): string {
  const today = new Date();
  const target = new Date(today);
  target.setDate(today.getDate() - (TODAY_INDEX - dayIndex));
  return `${target.getMonth() + 1}/${target.getDate()}`;
}

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function blank(): MessagesByDay {
  return Object.fromEntries(
    DAY_LABELS.map((label) => [label, []] as const),
  ) as unknown as MessagesByDay;
}

function blankWallpapers(): WallpapersByDay {
  return Object.fromEntries(
    DAY_LABELS.map((label) => [label, []] as const),
  ) as unknown as WallpapersByDay;
}

function dayLabelFor(createdAt: string): DayLabel | null {
  const created = new Date(createdAt);
  if (Number.isNaN(created.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  created.setHours(0, 0, 0, 0);
  const difference = Math.round(
    (today.getTime() - created.getTime()) / (24 * 60 * 60 * 1000),
  );
  if (difference >= 0 && difference <= TODAY_INDEX) {
    return DAY_LABELS[TODAY_INDEX - difference];
  }
  return null;
}

function loadMessages(): MessagesByDay {
  if (typeof window === "undefined") return blank();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as MessagesByDay;
  } catch {
    /* ignore */
  }
  return blank();
}

function persist(msgs: MessagesByDay) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(msgs));
  } catch {
    /* storage full */
  }
}

export type InitialInsertStatus = "idle" | "generating" | "ready" | "failed";

type State = {
  uiMode: UiMode;
  toggleUiMode: () => void;

  currentDayIndex: number;
  setCurrentDayIndex: (index: number) => void;
  currentWallpaperIndex: number;
  wallpapersByDay: WallpapersByDay;
  setWallpaperRevisions: (items: WallpaperItem[]) => void;
  setWallpaperInteractions: (items: WallpaperInteractionItem[]) => void;
  shiftWallpaper: (dir: -1 | 1) => void;
  messagesByDay: MessagesByDay;

  generatedWallpaperUrl: string;
  setGeneratedWallpaperUrl: (url: string) => void;
  wallpaperVersion: number;
  setWallpaperVersion: (version: number) => void;

  // Tracks whether the initial two-pass character insert (PreludeStep) has
  // completed. self-edit is only allowed when this is "ready".
  initialInsertStatus: InitialInsertStatus;
  setInitialInsertStatus: (s: InitialInsertStatus) => void;

  // True once the backend stage has been confirmed at least once for the
  // current session. While false, the interaction mode is "loading" so the
  // central button cannot flash on a refresh of a fully-completed wallpaper.
  wallpaperStateHydrated: boolean;
  setWallpaperStateHydrated: (v: boolean) => void;

  // Session ID: incremented when entering a new wallpaper flow so that
  // stale API responses from a previous flow can be detected and discarded.
  generationSessionId: string;
  incrementGenerationSession: () => string;

  // Expected eventSeq for historical page auto-jump: set when a historical
  // voice interaction succeeds, cleared when the revision arrives or on failure.
  // Bound to relationshipId to prevent cross-relationship pollution.
  expectedJumpEventSeq: number;
  expectedJumpRelationshipId: string;
  setExpectedJumpEventSeq: (seq: number | null, relationshipId: string) => void;

  wallpaperEnvelope: WallpaperEnvelope;
  setWallpaperEnvelope: (env: WallpaperEnvelope) => void;

  focusMode: FocusMode;
  setFocusMode: (mode: FocusMode) => void;

  addMessage: (msg: NewMessage) => void;
  clearMessages: () => void;
};

type WallpaperSelectionState = Pick<
  State,
  "currentDayIndex" | "currentWallpaperIndex" | "wallpapersByDay"
>;

export function getSelectedWallpaper(
  state: WallpaperSelectionState,
): WallpaperItem | undefined {
  const label = DAY_LABELS[state.currentDayIndex];
  return state.wallpapersByDay[label]?.[state.currentWallpaperIndex];
}

export function isLatestWallpaper(state: WallpaperSelectionState): boolean {
  const todayCount = state.wallpapersByDay[DAY_LABELS[TODAY_INDEX]]?.length ?? 0;
  return (
    state.currentDayIndex === TODAY_INDEX &&
    (todayCount === 0 || state.currentWallpaperIndex === todayCount - 1)
  );
}

export function isFirstWallpaper(state: WallpaperSelectionState): boolean {
  // Find the first non-empty day in the 7-day window
  let firstNonEmptyDayIndex: number | null = null;
  for (let i = 0; i < DAY_LABELS.length; i++) {
    const items = state.wallpapersByDay[DAY_LABELS[i]];
    if (items && items.length > 0) {
      firstNonEmptyDayIndex = i;
      break;
    }
  }
  if (firstNonEmptyDayIndex === null) {
    // No revisions at all — no older pages exist
    return true;
  }
  return (
    state.currentDayIndex === firstNonEmptyDayIndex &&
    state.currentWallpaperIndex === 0
  );
}

/**
 * Determines the current wallpaper interaction mode.
 *
 *   loading          — backend stage has not yet been resolved for the
 *                      current session. No button is shown; SubjectLift is
 *                      disabled; previous wallpaper image is preserved.
 *   history_disabled — user is viewing a past day or an older revision of
 *                      today. Neither entry is active. Left/right history
 *                      navigation continues to work normally.
 *   initial_voice    — base wallpaper, first voice not yet done.
 *                      Central microphone button is shown. SubjectLift is
 *                      disabled.
 *   processing       — first voice recording/transcribing/editing in
 *                      progress (initialInsertStatus === "generating").
 *                      Central button shows loading state. SubjectLift is
 *                      disabled.
 *   subject_lift     — first voice complete, full shared wallpaper shown.
 *                      Central button is NOT rendered. SubjectLift is active.
 */
export type InteractionMode =
  | "loading"
  | "history_disabled"
  | "initial_voice"
  | "processing"
  | "subject_lift";

export type WallpaperInteractionState = Pick<
  State,
  | "initialInsertStatus"
  | "currentDayIndex"
  | "currentWallpaperIndex"
  | "wallpapersByDay"
  | "wallpaperStateHydrated"
>;

export function getWallpaperInteractionMode(
  state: WallpaperInteractionState,
): InteractionMode {
  // Backend stage not yet resolved — keep current display, no entry active.
  if (!state.wallpaperStateHydrated) {
    return "loading";
  }

  // Past day or older revision — neither entry is active.
  if (!isLatestWallpaper(state)) {
    return "history_disabled";
  }

  // Latest, but first voice still pending.
  if (
    state.initialInsertStatus === "idle" ||
    state.initialInsertStatus === "failed"
  ) {
    return "initial_voice";
  }

  // First voice recording/transcribing/editing in progress.
  if (state.initialInsertStatus === "generating") {
    return "processing";
  }

  // First voice done — full shared wallpaper.
  return "subject_lift";
}

export const useSceneStore = create<State>((set, get) => ({
  uiMode: "wallpaper",
  toggleUiMode() {
    set((s) => ({ uiMode: s.uiMode === "wallpaper" ? "white" : "wallpaper" }));
  },

  currentDayIndex: TODAY_INDEX,
  currentWallpaperIndex: 0,
  wallpapersByDay: blankWallpapers(),
  setCurrentDayIndex(index: number) {
    if (index >= 0 && index < DAY_LABELS.length) {
      const label = DAY_LABELS[index];
      set({
        currentDayIndex: index,
        currentWallpaperIndex: Math.max(
          0,
          get().wallpapersByDay[label].length - 1,
        ),
        focusMode: "balanced",
      });
    }
  },
  messagesByDay: loadMessages(),

  setWallpaperRevisions(items) {
    const previous = get();

    // Defense-in-depth: the legacy frontend used to seed the store with
    // /wallpaper/today-bg.jpg as a demo placeholder. The 2027 backend
    // never produces that URL, but we keep filtering it so a stray row
    // in a migrated DB can never resurrect the deleted static asset.
    const LEGACY_DEMO_WALLPAPER = "/wallpaper/today-bg.jpg";
    const filteredItems = items.filter(
      (item) => item.imageUrl !== LEGACY_DEMO_WALLPAPER,
    );

    // A wallpaper revision is also the authoritative record of the voice event
    // that produced it. Sync that transcript so the partner device does not keep
    // generating suggestions from stale, device-local chat history.
    const syncedMessages: MessagesByDay = Object.fromEntries(
      DAY_LABELS.map((label) => [label, [...(previous.messagesByDay[label] ?? [])]]),
    ) as MessagesByDay;
    for (const item of filteredItems) {
      const transcript = item.transcript?.trim();
      const speakerRole = item.speakerRole;
      const label = dayLabelFor(item.createdAt);
      if (!transcript || !speakerRole || !label) continue;

      const timestamp = Date.parse(item.createdAt) || Date.now();
      const messageId = `wallpaper-event-${item.eventSeq}`;
      const messages = syncedMessages[label];
      const exactIndex = messages.findIndex((message) => message.id === messageId);
      const duplicateIndex = messages.findIndex(
        (message) =>
          message.from === speakerRole &&
          message.text.trim() === transcript &&
          Math.abs(message.timestamp - timestamp) < 10 * 60 * 1000,
      );
      const targetIndex = exactIndex >= 0 ? exactIndex : duplicateIndex;

      if (targetIndex >= 0) {
        messages[targetIndex] = {
          ...messages[targetIndex],
          id: messageId,
          from: speakerRole,
          text: transcript,
          timestamp,
          suggestedReply:
            item.reply?.trim() || messages[targetIndex].suggestedReply,
          suggestedReplies:
            item.suggestedReplies?.filter(Boolean).slice(0, 2) ||
            messages[targetIndex].suggestedReplies,
          relationshipSummary:
            item.relationshipSummary || messages[targetIndex].relationshipSummary,
        };
      } else {
        messages.push({
          id: messageId,
          type: "voice_message",
          from: speakerRole,
          audioUrl: "",
          text: transcript,
          durationSec: 0,
          timestamp,
          suggestedReply: item.reply?.trim() || undefined,
          suggestedReplies: item.suggestedReplies?.filter(Boolean).slice(0, 2),
          relationshipSummary: item.relationshipSummary,
        });
      }
      messages.sort((left, right) => left.timestamp - right.timestamp);
    }
    persist(syncedMessages);

    const selectedRevisionId = getSelectedWallpaper(previous)?.revisionId;
    const wasLatest = isLatestWallpaper(previous);
    const realItems: WallpapersByDay = Object.fromEntries(
      DAY_LABELS.map((label) => [label, []]),
    ) as unknown as WallpapersByDay;

    for (const item of [...filteredItems].sort((a, b) => a.eventSeq - b.eventSeq)) {
      const label = dayLabelFor(item.createdAt);
      if (
        label &&
        !realItems[label].some((entry) => entry.revisionId === item.revisionId)
      ) {
        realItems[label].push(item);
      }
    }

    // Revisions are the only source of truth for dated history. In particular,
    // an empty day from the backend must clear an older client-side bucket;
    // otherwise a newly generated current image can leak into yesterday or
    // the day-before through stale state retained by the speaking device.
    const grouped: WallpapersByDay = realItems;

    let nextDayIndex = previous.currentDayIndex;
    let nextWallpaperIndex = previous.currentWallpaperIndex;
    if (wasLatest) {
      nextDayIndex = TODAY_INDEX;
      nextWallpaperIndex = (grouped[DAY_LABELS[TODAY_INDEX]]?.length ?? 1) - 1;
    } else if (selectedRevisionId) {
      for (let dayIndex = 0; dayIndex < DAY_LABELS.length; dayIndex += 1) {
        const foundIndex = grouped[DAY_LABELS[dayIndex]].findIndex(
          (entry) => entry.revisionId === selectedRevisionId,
        );
        if (foundIndex >= 0) {
          nextDayIndex = dayIndex;
          nextWallpaperIndex = foundIndex;
          break;
        }
      }
    }
    const selectedDayItems = grouped[DAY_LABELS[nextDayIndex]];
    nextWallpaperIndex = Math.max(
      0,
      Math.min(nextWallpaperIndex, selectedDayItems.length - 1),
    );

    set({
      wallpapersByDay: grouped,
      messagesByDay: syncedMessages,
      currentDayIndex: nextDayIndex,
      currentWallpaperIndex: nextWallpaperIndex,
    });
  },

  setWallpaperInteractions(items) {
    if (!items.length) return;
    const previous = get();
    const syncedMessages: MessagesByDay = Object.fromEntries(
      DAY_LABELS.map((label) => [label, [...(previous.messagesByDay[label] ?? [])]]),
    ) as MessagesByDay;

    for (const item of items) {
      const transcript = item.transcript.trim();
      const label = dayLabelFor(item.createdAt);
      if (!transcript || !label) continue;

      const timestamp = Date.parse(item.createdAt) || Date.now();
      const messageId = `wallpaper-event-${item.eventSeq}`;
      const messages = syncedMessages[label];
      const exactIndex = messages.findIndex((message) => message.id === messageId);
      const duplicateIndex = messages.findIndex(
        (message) =>
          message.from === item.speakerRole &&
          message.text.trim() === transcript &&
          Math.abs(message.timestamp - timestamp) < 10 * 60 * 1000,
      );
      const targetIndex = exactIndex >= 0 ? exactIndex : duplicateIndex;
      const suggestedReply = item.reply.trim() || undefined;
      const suggestedReplies = item.suggestedReplies?.filter(Boolean).slice(0, 2);

      if (targetIndex >= 0) {
        const existing = messages[targetIndex];
        messages[targetIndex] = {
          ...existing,
          id: messageId,
          from: item.speakerRole,
          text: transcript,
          timestamp,
          suggestedReply: suggestedReply || existing.suggestedReply,
          suggestedReplies: suggestedReplies?.length
            ? suggestedReplies
            : existing.suggestedReplies,
          relationshipSummary:
            item.relationshipSummary || existing.relationshipSummary,
        };
      } else {
        messages.push({
          id: messageId,
          type: "voice_message",
          from: item.speakerRole,
          audioUrl: "",
          text: transcript,
          durationSec: 0,
          timestamp,
          suggestedReply,
          suggestedReplies,
          relationshipSummary: item.relationshipSummary,
        });
      }
      messages.sort((left, right) => left.timestamp - right.timestamp);
    }

    persist(syncedMessages);
    set({ messagesByDay: syncedMessages });
  },

  shiftWallpaper(dir: -1 | 1) {
    const state = get();
    let dayIndex = state.currentDayIndex;
    let wallpaperIndex = state.currentWallpaperIndex;
    const currentItems = state.wallpapersByDay[DAY_LABELS[dayIndex]] ?? [];

    if (dir === -1) {
      // Move to older wallpaper within current day
      if (wallpaperIndex > 0) {
        wallpaperIndex -= 1;
      } else {
        // Move to previous day, skipping empty buckets
        let found = false;
        for (let d = dayIndex - 1; d >= 0; d--) {
          const items = state.wallpapersByDay[DAY_LABELS[d]];
          if (items && items.length > 0) {
            dayIndex = d;
            wallpaperIndex = items.length - 1;
            found = true;
            break;
          }
        }
        // If no non-empty day found, stay at current position
        if (!found) return;
      }
    } else {
      // Move to newer wallpaper within current day
      if (wallpaperIndex < currentItems.length - 1) {
        wallpaperIndex += 1;
      } else {
        // Move to next day, skipping empty buckets
        let found = false;
        for (let d = dayIndex + 1; d < DAY_LABELS.length; d++) {
          const items = state.wallpapersByDay[DAY_LABELS[d]];
          if (items && items.length > 0) {
            dayIndex = d;
            wallpaperIndex = 0;
            found = true;
            break;
          }
        }
        // If no non-empty day found, stay at current position
        if (!found) return;
      }
    }

    set({
      currentDayIndex: dayIndex,
      currentWallpaperIndex: wallpaperIndex,
      focusMode: "balanced",
    });
  },

  generatedWallpaperUrl: "",
  setGeneratedWallpaperUrl(url: string) {
    if (!url) return;
    // The current display URL is not a dated revision. History is populated
    // exclusively by setWallpaperRevisions after the backend has persisted
    // the new revision, so never synthesize a local `live-*` history item.
    set({ generatedWallpaperUrl: url });
  },
  wallpaperVersion: 0,
  setWallpaperVersion(version) {
    set({ wallpaperVersion: Math.max(0, version) });
  },

  initialInsertStatus: "idle",
  setInitialInsertStatus(s) {
    set({ initialInsertStatus: s });
  },

  wallpaperStateHydrated: false,
  setWallpaperStateHydrated(v) {
    set({ wallpaperStateHydrated: v });
  },

  generationSessionId: "",
  incrementGenerationSession() {
    const next = crypto.randomUUID();
    set({ generationSessionId: next });
    return next;
  },

  expectedJumpEventSeq: 0,
  expectedJumpRelationshipId: "",
  setExpectedJumpEventSeq(seq: number | null, relationshipId: string = "") {
    set({
      expectedJumpEventSeq: seq ?? 0,
      expectedJumpRelationshipId: relationshipId,
    });
  },

  wallpaperEnvelope: { emotion: "calm", intensity: 0.5 },
  setWallpaperEnvelope(env: WallpaperEnvelope) {
    set({ wallpaperEnvelope: env });
  },

  focusMode: "balanced",
  setFocusMode(mode) {
    set({ focusMode: mode });
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
