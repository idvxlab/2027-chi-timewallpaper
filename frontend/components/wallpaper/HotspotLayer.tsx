"use client";

import { useSceneStore } from "@/lib/hooks/useSceneStore";
import { postTouch } from "@/lib/api";

export function HotspotLayer() {
  const hotspots = useSceneStore((s) => s.scene?.hotspots ?? []);

  return (
    <div className="absolute inset-0">
      {hotspots.map((h) => (
        <button
          key={h.id}
          className="absolute rounded-full focus:outline-none focus:ring-2 focus:ring-white/60"
          style={{
            left: `${h.x * 100}%`,
            top: `${h.y * 100}%`,
            width: `${h.r * 2 * 100}%`,
            height: `${h.r * 2 * 100}%`,
            transform: "translate(-50%, -50%)",
            background: "radial-gradient(circle, rgba(255,255,255,0.25), rgba(255,255,255,0))",
          }}
          onClick={() => postTouch({ hotspotId: h.id })}
          aria-label={h.label}
        />
      ))}
    </div>
  );
}
