import { websocketUrl } from "@/lib/config";

const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

const CHARACTER_ASSET_EVENTS_PATH = "/character-assets/current-relationship/events";

/** Subscribe to relationship-scoped character status changes without polling. */
export function subscribeToCharacterAssetEvents(
  onChange: () => void,
): () => void {
  let stopped = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;

  const connect = () => {
    if (stopped) return;
    socket = new WebSocket(websocketUrl(CHARACTER_ASSET_EVENTS_PATH));
    socket.onopen = () => {
      reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
    };
    socket.onmessage = () => {
      onChange();
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
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
    };
  };

  connect();
  return () => {
    stopped = true;
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    socket?.close();
  };
}
