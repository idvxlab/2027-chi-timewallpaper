"use client";

import { useSceneStore } from "@/lib/hooks/useSceneStore";

export function MemoryToast() {
  const toast = useSceneStore((s) => s.toast);

  if (!toast) return null;

  return (
    <div className="absolute left-1/2 -translate-x-1/2 bottom-8 px-4 py-2 rounded-2xl bg-black/60 text-white text-sm backdrop-blur">
      {toast}
    </div>
  );
}
