import type { ReactNode } from "react";

// Production layout renders the page content directly at full viewport.
// The iPad mockup frame was removed so the wallpaper and onboarding
// screens occupy the full window. This component is kept (as a thin
// pass-through) so existing call-sites continue to compile, but it
// contributes no visual chrome.
export function IpadFrame({ children }: { children: ReactNode }) {
  return <>{children}</>;
}