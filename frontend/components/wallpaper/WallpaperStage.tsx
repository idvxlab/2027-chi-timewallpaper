"use client";

import { AtmosphereLayer } from "./AtmosphereLayer";
import { HotspotLayer } from "./HotspotLayer";
import { MemoryToast } from "./MemoryToast";
import { useWebSocket } from "@/lib/hooks/useWebSocket";

export function WallpaperStage() {
  useWebSocket();

  return (
    <div className="wallpaper-root">
      <AtmosphereLayer />
      <HotspotLayer />
      <MemoryToast />
    </div>
  );
}
