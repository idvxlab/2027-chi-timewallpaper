import type { ReactNode } from "react";

export function IpadFrame({ children }: { children: ReactNode }) {
  return (
    <div className="relative rounded-[40px] border-[10px] border-slate-800 bg-slate-900 shadow-2xl overflow-hidden">
      <div className="relative aspect-[9/16] w-[820px] max-w-full">
        {children}
      </div>
    </div>
  );
}
