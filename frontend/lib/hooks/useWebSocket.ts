"use client";

import { getScene } from "@/lib/api";
import { WS_PATH } from "@/lib/constants";
import { useEffect, useRef } from "react";
import { useSceneStore } from "./useSceneStore";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const applyEvent = useSceneStore((s) => s.applyEvent);
  const setScene = useSceneStore((s) => s.setScene);

  function connect() {
    if (wsRef.current) return;

    const wsBase =
      typeof window !== "undefined"
        ? (process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000")
        : "ws://localhost:8000";

    const ws = new WebSocket(`${wsBase}${WS_PATH}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        // WS 推送的事件可能是纯 SceneEvent 或 { scene: Scene }
        if (ev.scene) {
          applyEvent({ scene: ev.scene });
        } else {
          applyEvent(ev);
        }
      } catch {
        /* 忽略坏包 */
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      // 3s 后自动重连
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }

  function disconnect() {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    wsRef.current?.close();
    wsRef.current = null;
  }

  useEffect(() => {
    // 首次连接:拉当前 scene + 建 WS
    getScene()
      .then(setScene)
      .catch(() => null);
    connect();

    return () => disconnect();
  }, []);
}
