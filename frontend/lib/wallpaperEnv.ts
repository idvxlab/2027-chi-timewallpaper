// Wallpaper envelope — the visual interpretation layer between the
// mock pipeline output and the wallpaper rendering surface.
//
// The pipeline produces semantic metadata (emotion + intensity). This
// module translates that semantic state into a set of CSS filter tokens
// that the existing AtmosphereLayer can apply directly. There is no
// image manipulation here — the wallpaper itself is never touched —
// only the way it is being seen.
//
// Design constraints:
//   - Output stays inside filter: brightness() saturate() contrast()
//     hue-rotate() sepia(); no opacity layering, no overlays, no SVG.
//   - All values are clamped and continuous; no discrete steps.
//   - Same input -> same output (pure, deterministic, mockable).

export type WallpaperEnvelope = {
  emotion:
    | "soft longing"
    | "calm"
    | "warmth"
    | "tenderness"
    | "quiet gratitude"
    | "gentle pride"
    | "reverie"
    | "grounded affection";
  intensity: number; // 0..1
};

// Emotion -> "lighting bias" tokens.
// The numbers are intentionally small (delta ranges ~ 0.04-0.10).
// They sit on top of a neutral baseline and never overwhelm the image.
const EMOTION_BIAS: Record<
  WallpaperEnvelope["emotion"],
  { brightness: number; saturate: number; hueDeg: number; sepia: number }
> = {
  "soft longing": {
    brightness: 1.04, // slightly warmer lift
    saturate: 0.95, // a touch of de-saturation for distance
    hueDeg: -8, // amber tilt
    sepia: 0.08, // low-warm wash
  },
  warmth: {
    brightness: 1.08, // brighter highlights
    saturate: 1.08, // richer color
    hueDeg: 4, // gentle warm push
    sepia: 0.05,
  },
  calm: {
    brightness: 1.0,
    saturate: 1.0,
    hueDeg: 0,
    sepia: 0.0,
  },
  tenderness: {
    brightness: 1.05,
    saturate: 1.04,
    hueDeg: 2,
    sepia: 0.06,
  },
  "quiet gratitude": {
    brightness: 1.06,
    saturate: 1.02,
    hueDeg: -4,
    sepia: 0.04,
  },
  "gentle pride": {
    brightness: 1.07,
    saturate: 1.1,
    hueDeg: 6,
    sepia: 0.0,
  },
  reverie: {
    brightness: 1.02,
    saturate: 0.92,
    hueDeg: -12, // cooler / dreamier tilt
    sepia: 0.05,
  },
  "grounded affection": {
    brightness: 1.03,
    saturate: 1.0,
    hueDeg: 0,
    sepia: 0.07,
  },
};

const NEUTRAL = {
  brightness: 1.0,
  saturate: 1.0,
  contrast: 1.0,
  sepia: 0.0,
  hueDeg: 0,
};

function clamp01(n: number): number {
  if (Number.isNaN(n)) return 0.5;
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

export function envelopeToFilter(env: WallpaperEnvelope): string {
  const intensity = clamp01(env.intensity);
  const bias = EMOTION_BIAS[env.emotion] ?? EMOTION_BIAS.calm;

  // Intensity modulates two axes:
  //   - contrast: low intensity -> softer / lower contrast (0.4 -> ~0.92),
  //               high intensity -> slightly punchier (1.0 -> ~1.06).
  //   - saturation: rising very gently with intensity as well, so a
  //                 high-intensity emotional state reads as slightly more
  //                 present without overshooting.
  //
  // Both axes interpolate linearly across the 0..1 range; they are
  // clamped so 0.4 still gives a soft image and 1.0 stays subtle.
  const contrast = 0.92 + (intensity - 0.4) * (0.14 / 0.6); // 0.92 -> 1.06 over [0.4, 1.0]
  const saturate = bias.saturate + (intensity - 0.5) * 0.12; // +/- 0.06 around bias
  const brightness = bias.brightness;
  const sepia = bias.sepia;
  const hueDeg = bias.hueDeg;

  // Safety: collapse to neutral if for any reason emotions resolve
  // outside the closed vocabulary.
  const safe =
    Number.isFinite(contrast) &&
    Number.isFinite(saturate) &&
    Number.isFinite(brightness);

  if (!safe) {
    return `brightness(${NEUTRAL.brightness}) saturate(${NEUTRAL.saturate}) contrast(${NEUTRAL.contrast})`;
  }

  return [
    `brightness(${brightness.toFixed(3)})`,
    `saturate(${saturate.toFixed(3)})`,
    `contrast(${contrast.toFixed(3)})`,
    `sepia(${sepia.toFixed(3)})`,
    `hue-rotate(${hueDeg.toFixed(1)}deg)`,
  ].join(" ");
}
