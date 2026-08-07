// Mock pipeline: voice → meaning → wallpaper.
//
// This module exists to bridge the Prelude step into the Wallpaper stage
// without a real backend. It is a pure, synchronous derivation: the same
// (role, transcript) input always produces the same emotional metadata
// and the same asset URL, so HCI demonstrations are reproducible.
//
// The pipeline is intentionally cheap: it does not parse language, does
// not call any network, and does not run async work. It encodes the
// narrative relationship between role and transcript as a small lookup
// over the closed vocabulary that the onboarding flow already exposes.

export type MockRole = "elder" | "child" | null;

export type MockEmotion =
  | "soft longing"
  | "calm"
  | "warmth"
  | "tenderness"
  | "quiet gratitude"
  | "gentle pride"
  | "reverie"
  | "grounded affection";

export type MockWallpaperResult = {
  imageUrl: string;
  metadata: {
    emotion: MockEmotion;
    intensity: number;
  };
};

// Single shared asset. The wallpaper stage's AtmosphereLayer consumes
// whatever is in useSceneStore.generatedWallpaperUrl and renders it as
// a background image; pointing Prelude at one local SVG keeps the
// visual handshake deterministic for the demo.
const MOCK_WALLPAPER_URL = "/demo-wallpaper.svg";

const ROLE_EMOTION: Record<Exclude<MockRole, null>, MockEmotion> = {
  elder: "soft longing",
  child: "warmth",
};

const TRANSCRIPT_LEXICON: { match: RegExp; emotion: MockEmotion; boost: number }[] = [
  { match: /think about|remember|miss|recall|memory|past|used to/i, emotion: "soft longing", boost: 0.15 },
  { match: /love|heart|care|tender|hold|warm/i, emotion: "tenderness", boost: 0.2 },
  { match: /thank|grateful|appreciate/i, emotion: "quiet gratitude", boost: 0.18 },
  { match: /proud|grown|achieve|learn/i, emotion: "gentle pride", boost: 0.18 },
  { match: /dream|wonder|imagine|future/i, emotion: "reverie", boost: 0.16 },
  { match: /safe|home|calm|peace|rest/i, emotion: "grounded affection", boost: 0.14 },
];

function deriveEmotion(
  role: MockRole,
  transcript: string,
): { emotion: MockEmotion; intensity: number } {
  const baseEmotion: MockEmotion =
    role && role in ROLE_EMOTION
      ? ROLE_EMOTION[role as Exclude<MockRole, null>]
      : "calm";

  let emotion: MockEmotion = baseEmotion;
  let intensity = 0.55;

  for (const rule of TRANSCRIPT_LEXICON) {
    if (rule.match.test(transcript)) {
      emotion = rule.emotion;
      intensity = Math.min(1, 0.55 + rule.boost);
      break;
    }
  }

  // Empty or very short transcripts keep the base emotion with lower
  // intensity — the system has not heard enough to commit to anything
  // specific.
  const trimmed = transcript.trim();
  if (trimmed.length < 8) {
    return { emotion: baseEmotion, intensity: 0.4 };
  }

  return { emotion, intensity };
}

export function mockGenerateWallpaper(input: {
  role: MockRole;
  transcript: string;
}): MockWallpaperResult {
  const { emotion, intensity } = deriveEmotion(input.role, input.transcript);
  return {
    imageUrl: MOCK_WALLPAPER_URL,
    metadata: {
      emotion,
      intensity: Number(intensity.toFixed(2)),
    },
  };
}
