"use client";

import { DEMO_PRESETS } from "@/lib/constants";
import { switchDemo } from "@/lib/api";
import { useSceneStore } from "@/lib/hooks/useSceneStore";

export function DemoSwitcher() {
  const current = useSceneStore((s) => s.scene?.demoId);
  const setScene = useSceneStore((s) => s.setScene);

  return (
    <div className="flex gap-2">
      {DEMO_PRESETS.map((d) => (
        <button
          key={d.id}
          onClick={() => switchDemo(d.id).then(setScene).catch(() => undefined)}
          className={`px-3 py-1.5 rounded-full text-sm border transition ${
            current === d.id
              ? "bg-slate-900 text-white border-slate-900"
              : "bg-white text-slate-900 border-slate-200 hover:bg-slate-50"
          }`}
        >
          {d.label}
        </button>
      ))}
    </div>
  );
}
