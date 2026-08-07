import { websocketUrl } from "@/lib/config";
import type { MemoryObjectItem } from "@/lib/api";

const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

const WALLPAPER_EVENTS_PATH = "/wallpapers/current/events";

export type WallpaperEvent = {
  type: "wallpaper_state_changed";
  relationshipId: string;
  status: string;
  version?: number;
  imageUrl?: string;
  error?: string;
  eventId?: string;
  transcript?: string;
  reply?: string;
  suggestedReplies?: string[];
  memoryObject?: MemoryObjectItem;
  speakerRole?: "child" | "elder";
  createdAt?: string;
};

/** Subscribe to relationship-scoped wallpaper changes without HTTP polling. */
export function subscribeToWallpaperEvents(
  onChange: (event: WallpaperEvent) => void,
): () => void {
  let stopped = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;

  const connect = () => {
    if (stopped) return;
    socket = new WebSocket(websocketUrl(WALLPAPER_EVENTS_PATH));
    socket.onopen = () => {
      reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
    };
    socket.onmessage = (message) => {
      try {
        onChange(JSON.parse(String(message.data)) as WallpaperEvent);
      } catch {
        /* Ignore malformed event frames and keep the connection alive. */
      }
    };
    socket.onerror = () => {
      socket?.close();
    };
    socket.onclose = (event) => {
      socket = null;
      if (stopped || event.code === 4401) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, reconnectDelay);
      reconnectDelay = Math.min(
        reconnectDelay * 2,
        MAX_RECONNECT_DELAY_MS,
      );
    };
  };

  connect();
  return () => {
    stopped = true;
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    socket?.close();
  };
}
