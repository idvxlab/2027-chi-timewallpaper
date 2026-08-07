import { websocketUrl } from "@/lib/config";

const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

const RELATIONSHIP_EVENTS_PATH = "/relationships/current/events";

type RelationshipEvent = {
  type: "relationship_status" | "relationship_connected";
  relationshipId: string;
  status: "waiting" | "connected";
};

/** Listen for the other family member joining without polling the session API. */
export function subscribeToRelationshipEvents(
  onConnected: () => void,
  onConnectionError?: () => void,
): () => void {
  let stopped = false;
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;

  const connect = () => {
    if (stopped) return;
    socket = new WebSocket(websocketUrl(RELATIONSHIP_EVENTS_PATH));
    socket.onopen = () => {
      reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
    };
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as RelationshipEvent;
        if (event.status === "connected") onConnected();
      } catch (error) {
        console.error("[RelationshipEvents] invalid event", error);
      }
    };
    socket.onerror = () => {
      onConnectionError?.();
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
