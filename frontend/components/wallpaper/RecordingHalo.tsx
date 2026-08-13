"use client";

/**
 * RecordingHalo
 *
 * Visual feedback for the new "Hold Anywhere to Record" interaction.
 *
 * States (driven by `phase`):
 *   - "starting" : halo just appeared (recorder start in flight).
 *                  Static / very slow pulse so the user sees instant feedback
 *                  without a "flash of bright animation".
 *   - "recording" : microphone is live. Subtle pulse + outer ripple.
 *   - "exiting"   : finger lifted; halo fades out over ~160ms.
 *
 * Visual goals (per design spec):
 *   - Apple / large-company restraint.
 *   - White ring (rgba 255,255,255,0.88–0.96) with soft glow.
 *   - Pulse + sonar-ripple animation using transform/opacity only
 *     (GPU-friendly, no per-frame filter / blur animation).
 *   - Always anchored to the pointerDown clientX/Y (in viewport coords).
 *   - NEVER affects wallpaper transform or layer ordering.
 */

import type { CSSProperties } from "react";

const HALO_DIAMETER_PX = 72;

export type RecordingHaloPhase = "starting" | "recording" | "exiting";

type Props = {
  /** Pointer-down viewport coordinates (clientX, clientY). */
  clientX: number;
  clientY: number;
  phase: RecordingHaloPhase;
};

export function RecordingHalo({ clientX, clientY, phase }: Props) {
  // Use a viewport-anchored transform so the halo is independent of the
  // wallpaper layer's translate3d (which can shift under carousel gesture).
  const style: CSSProperties = {
    position: "fixed",
    left: clientX,
    top: clientY,
    width: HALO_DIAMETER_PX,
    height: HALO_DIAMETER_PX,
    marginLeft: -HALO_DIAMETER_PX / 2,
    marginTop: -HALO_DIAMETER_PX / 2,
    pointerEvents: "none",
    zIndex: 50,
    transform: "translate3d(0,0,0)",
    willChange: "transform, opacity",
  };

  return (
    <div
      data-testid="recording-halo"
      data-phase={phase}
      style={style}
      aria-hidden="true"
    >
      <HaloRing phase={phase} />
      <HaloRipple phase={phase} delayMs={0} />
      <HaloRipple phase={phase} delayMs={600} />
    </div>
  );
}

function HaloRing({ phase }: { phase: RecordingHaloPhase }) {
  const cls = [
    "halo-ring",
    `halo-ring--${phase}`,
  ].join(" ");
  return <div className={cls} />;
}

function HaloRipple({
  phase,
  delayMs,
}: {
  phase: RecordingHaloPhase;
  delayMs: number;
}) {
  const cls = ["halo-ripple", `halo-ripple--${phase}`].join(" ");
  return <div className={cls} style={{ animationDelay: `${delayMs}ms` }} />;
}