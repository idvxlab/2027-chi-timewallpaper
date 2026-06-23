"use client";

import { useSceneStore } from "@/lib/hooks/useSceneStore";

const BASE_WALLPAPERS: Record<string, string> = {
  前天: "/wallpaper/yesterday-bg.jpg",
  昨天: "/wallpaper/yesterday-bg.jpg",
  今天: "/wallpaper/today-bg.jpg",
};

export function AtmosphereLayer() {
  const scene = useSceneStore((s) => s.scene);
  const currentDayIndex = useSceneStore((s) => s.currentDayIndex);
  const generatedWallpaperUrl = useSceneStore((s) => s.generatedWallpaperUrl);
  const dayLabel = ["前天", "昨天", "今天"][currentDayIndex];
  const baseWallpaper = BASE_WALLPAPERS[dayLabel] ?? "/wallpaper/neutral-base.jpg";
  const wallpaper = currentDayIndex === 2 && generatedWallpaperUrl ? generatedWallpaperUrl : baseWallpaper;

  return (
    <div className="absolute inset-0">
      <img
        src={wallpaper}
        alt=""
        className="w-full h-full object-cover"
      />
      {scene?.glow && (
        <img
          src="/wallpaper/window-glow.png"
          alt=""
          className="absolute inset-0 w-full h-full object-cover mix-blend-screen"
          style={{ opacity: scene.glowOpacity ?? 0.6 }}
        />
      )}
      <img
        src="/wallpaper/cloud-overlay.png"
        alt=""
        className="absolute inset-0 w-full h-full object-cover pointer-events-none"
      />
      <img
        src="/wallpaper/vignette.png"
        alt=""
        className="absolute inset-0 w-full h-full object-cover pointer-events-none"
      />
    </div>
  );
}
