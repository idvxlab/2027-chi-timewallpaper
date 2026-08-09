import { apiUrl, assetUrl, websocketUrl } from "@/lib/config";

/**
 * Exposed for legacy readers. New code should prefer `apiUrl(path)` and
 * `assetUrl(path)` from `@/lib/config`. In production this resolves to an
 * empty string so the same-origin path-based fetch is used.
 */
export const API_BASE = (() => {
  const raw = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").trim();
  if (raw.startsWith("http://") || raw.startsWith("https://"))
    return raw.replace(/\/$/, "");
  return "";
})();

export const STATIC_BASE_SCENE_PATH = "/generated/background.png";

const LEGACY_BASE_SCENE_PATHS = new Set([
  "/generated/wallpaper-main-square-1536.png",
  "/wallpaper/today-bg.jpg",
]);

export type OnboardingViewerRole = "elder" | "child";
export type OnboardingGender = "male" | "female";

export type OnboardingProfileInput = {
  viewerRole: OnboardingViewerRole;
  displayName: string;
  gender: OnboardingGender;
  userId?: string;
  relationshipId?: string;
};

export type OnboardingProfileResult = {
  userId: string;
  counterpartUserId: string;
  relationshipId: string;
  relationshipDisplayName: string;
  inviteCode: string | null;
  relationshipStatus: "waiting" | "connected";
  viewerRole: OnboardingViewerRole;
  counterpartRole: OnboardingViewerRole;
  familyRole: "mother" | "father" | "daughter" | "son";
  displayName: string;
  gender: OnboardingGender;
};

export async function createOnboardingProfile(
  input: OnboardingProfileInput,
): Promise<OnboardingProfileResult> {
  const response = await fetch(apiUrl("/onboarding/profile"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(input),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Profile setup failed (${response.status}): ${detail || response.statusText}`,
    );
  }

  return (await response.json()) as OnboardingProfileResult;
}

export type RelationshipInvitationResult = {
  inviteCode: string;
  relationshipId: string;
  relationshipDisplayName: string;
  creatorRole: OnboardingViewerRole;
  requiredRole: OnboardingViewerRole;
  status: "waiting";
};

export async function getRelationshipInvitation(
  inviteCode: string,
): Promise<RelationshipInvitationResult> {
  const response = await fetch(
    apiUrl(`/relationships/invitations/${encodeURIComponent(inviteCode)}`),
  );
  if (!response.ok) {
    throw new Error(`Invitation lookup failed (${response.status})`);
  }
  return (await response.json()) as RelationshipInvitationResult;
}

export type JoinRelationshipInput = {
  inviteCode: string;
  viewerRole: OnboardingViewerRole;
  displayName: string;
  gender: OnboardingGender;
};

export async function joinRelationship(
  input: JoinRelationshipInput,
): Promise<OnboardingProfileResult> {
  const response = await fetch(apiUrl("/relationships/join"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Relationship join failed (${response.status}): ${detail || response.statusText}`,
    );
  }
  return (await response.json()) as OnboardingProfileResult;
}

export type SessionResult = OnboardingProfileResult & {
  onboardingStep: "pairing" | "photo" | "connected" | "prelude" | "wallpaper";
  wallpaperUrl: string;
};

export async function getCurrentSession(): Promise<SessionResult | null> {
  const response = await fetch(apiUrl("/sessions/me"), {
    credentials: "include",
    cache: "no-store",
  });
  if (response.status === 401) return null;
  if (!response.ok) {
    throw new Error(`Session restore failed (${response.status})`);
  }
  return (await response.json()) as SessionResult;
}

export async function updateSessionProgress(input: {
  onboardingStep: "photo" | "prelude" | "wallpaper";
  wallpaperUrl?: string;
}): Promise<SessionResult> {
  const response = await fetch(apiUrl("/sessions/me/progress"), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(`Session progress update failed (${response.status})`);
  }
  return (await response.json()) as SessionResult;
}

export async function logoutSession(): Promise<void> {
  const response = await fetch(apiUrl("/sessions/logout"), {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok && response.status !== 204) {
    throw new Error(`Session logout failed (${response.status})`);
  }
}

export type ExperimentEventInput = {
  eventName: string;
  sceneId?: string;
  updateId?: string;
  payload?: Record<string, unknown>;
};

/** Record a browser-side study event using the authenticated session identity. */
export async function recordExperimentEvent(
  input: ExperimentEventInput,
): Promise<void> {
  const now = new Date();
  const timezoneOffsetMinutes = -now.getTimezoneOffset();
  const local = new Date(now.getTime() + timezoneOffsetMinutes * 60_000);
  const studyDay = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");

  const response = await fetch(apiUrl("/experiment-events"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    keepalive: true,
    body: JSON.stringify({
      eventName: input.eventName,
      occurredAtUtc: now.toISOString(),
      occurredAtLocal: local.toISOString().replace(/Z$/, ""),
      timezoneName:
        Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown",
      timezoneOffsetMinutes,
      studyDay,
      sceneId: input.sceneId || "",
      updateId: input.updateId || "",
      payload: input.payload || {},
    }),
  });
  if (!response.ok) {
    throw new Error(`Experiment event failed (${response.status})`);
  }
}

export type CharacterAsset = {
  assetId: string;
  userId: string;
  relationshipId: string | null;
  role: "elder" | "child";
  status: "processing" | "ready" | "failed";
  sourceImageUrl: string;
  masterImageUrl: string;
  portraitImageUrl: string;
  halfBodyImageUrl: string;
  fullBodyImageUrl: string;
  isActive: boolean;
};

export type RelationshipCharacterAssets = {
  relationshipId: string;
  ready: boolean;
  elder: CharacterAsset | null;
  child: CharacterAsset | null;
};

export function resolveApiAssetUrl(path: string): string {
  return assetUrl(path);
}

/** Replace base-scene URLs persisted by older frontend/backend versions. */
export function normalizeWallpaperImageUrl(path: string): string {
  const value = path.trim();
  if (!value) return "";

  let pathname = value;
  try {
    pathname = new URL(value, "http://timewallpaper.local").pathname;
  } catch {
    // Keep the original value when it is not a parseable URL.
  }

  return LEGACY_BASE_SCENE_PATHS.has(pathname)
    ? resolveApiAssetUrl(STATIC_BASE_SCENE_PATH)
    : pathname.startsWith("/generated/")
      ? resolveApiAssetUrl(value)
      : value;
}

export async function uploadMyCharacterAsset(
  image: File,
): Promise<CharacterAsset> {
  const form = new FormData();
  form.append("image", image, image.name || "portrait.jpg");
  const response = await fetch(apiUrl("/character-assets/me"), {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Character upload failed (${response.status}): ${detail || response.statusText}`,
    );
  }
  return (await response.json()) as CharacterAsset;
}

export async function getCurrentRelationshipCharacterAssets(): Promise<RelationshipCharacterAssets> {
  const response = await fetch(
    apiUrl("/character-assets/current-relationship"),
    {
      credentials: "include",
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(`Character status failed (${response.status})`);
  }
  return (await response.json()) as RelationshipCharacterAssets;
}

export type MemoryObjectItem = {
  assetId: string;
  name: string;
  mentionCount: number;
  threshold: number;
  ready: boolean;
  imageUrl: string;
  prompt: string;
  examples: string[];
  sourceMessageIds: string[];
  lastSeenAt: string | null;
};

export type MemoryObjectsResult = {
  runId: string;
  status: string;
  userId: string;
  relationshipId: string;
  threshold: number;
  items: MemoryObjectItem[];
};

export async function getMemoryObjects(input: {
  userId: string;
  relationshipId: string;
  threshold?: number;
  limit?: number;
  generateMissing?: boolean;
}): Promise<MemoryObjectsResult> {
  const response = await fetch(apiUrl("/agent-runs/memory-objects"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      userId: input.userId,
      relationshipId: input.relationshipId,
      threshold: input.threshold ?? 3,
      limit: input.limit ?? 12,
      generateMissing: input.generateMissing ?? false,
    }),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Memory objects failed (${response.status}): ${detail || response.statusText}`,
    );
  }
  return (await response.json()) as MemoryObjectsResult;
}

export type UploadAudioResult = {
  transcript: string;
  imageUrl: string;
};

export async function uploadAudio(blob: Blob): Promise<UploadAudioResult> {
  const form = new FormData();
  const filename = `voice-${Date.now()}.wav`;
  form.append("audio", blob, filename);

  const res = await fetch(apiUrl("/asr"), {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data?.detail) detail = String(data.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  return (await res.json()) as UploadAudioResult;
}

/** Pure speech-to-text for the PreludeStep (Step 2) flow.
 *  Hits the new POST /asr/transcribe endpoint, which only transcribes
 *  audio — it does NOT trigger wallpaper generation. */
export type TranscribeAudioResult = {
  transcript: string;
  raw?: unknown;
  /** True when the response was fabricated because ASR failed. */
  fallback: boolean;
};

export async function transcribeAudio(
  audioBlob: Blob,
): Promise<TranscribeAudioResult> {
  console.log("[api.transcribeAudio] POST /asr/transcribe start");
  console.log(
    "[api.transcribeAudio] audio size/type =",
    audioBlob.size,
    "/",
    audioBlob.type,
  );

  const form = new FormData();
  const filename = "recording.webm";
  form.append("audio", audioBlob, filename);

  const controller = new AbortController();
  const timeoutMs = 45_000;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(apiUrl("/asr/transcribe"), {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Transcription timed out after ${timeoutMs}ms`);
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
  window.clearTimeout(timer);

  console.log("[api.transcribeAudio] response status =", res.status);

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.text();
      console.error("[api.transcribeAudio] backend error body =", body);
      detail = `Transcription failed ${res.status}: ${body}`;
    } catch (err) {
      console.error("[api.transcribeAudio] failed to read error body:", err);
    }
    throw new Error(detail);
  }

  const json = (await res.json()) as TranscribeAudioResult;
  console.log("[api.transcribeAudio] response json =", json);
  return json;
}

export type CurrentWallpaper = {
  relationshipId: string;
  viewerRole: OnboardingViewerRole;
  stage: string;
  status:
    | "idle"
    | "generating"
    | "generating_partner"
    | "partner_failed"
    | "failed";
  version: number;
  baseSceneUrl: string;
  wallpaperUrl: string;
  latestRunId: string;
  error: string;
};

export async function getCurrentWallpaper(): Promise<CurrentWallpaper> {
  const response = await fetch(apiUrl("/wallpapers/current"), {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Current wallpaper lookup failed (${response.status})`);
  }
  return (await response.json()) as CurrentWallpaper;
}

export type WallpaperRevision = {
  revisionId: string;
  eventSeq: number;
  imageUrl: string;
  createdAt: string;
  transcript: string;
  reply: string;
  suggestedReplies: string[];
  relationshipSummary: RelationshipSummary;
  speakerRole: OnboardingViewerRole | null;
};

export type WallpaperRevisionList = {
  relationshipId: string;
  viewerRole: OnboardingViewerRole;
  items: WallpaperRevision[];
};

export async function getWallpaperRevisions(): Promise<WallpaperRevisionList> {
  const response = await fetch(apiUrl("/wallpapers/revisions"), {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Wallpaper revision lookup failed (${response.status})`);
  }
  return (await response.json()) as WallpaperRevisionList;
}

export type WallpaperInteraction = {
  eventId: string;
  eventSeq: number;
  status: string;
  transcript: string;
  reply: string;
  suggestedReplies: string[];
  relationshipSummary: RelationshipSummary;
  speakerRole: OnboardingViewerRole;
  createdAt: string;
};

export type RelationshipSummary = {
  themeZh?: string;
  themeEn?: string;
  descriptionZh?: string;
  descriptionEn?: string;
  counterpartRole?: OnboardingViewerRole;
  spatialMode?: string;
};

export type WallpaperInteractionList = {
  relationshipId: string;
  items: WallpaperInteraction[];
};

/** ChatBot results are available before the corresponding image revision. */
export async function getWallpaperInteractions(): Promise<WallpaperInteractionList> {
  const response = await fetch(apiUrl("/wallpapers/interactions"), {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Wallpaper interaction lookup failed (${response.status})`);
  }
  return (await response.json()) as WallpaperInteractionList;
}

export type FirstVoiceWallpaperViews = {
  childViewUrl: string;
  elderViewUrl: string;
  speakerRole: OnboardingViewerRole;
};

export type FirstVoiceAgentRunResult = {
  runId: string;
  status: string;
  requestId?: string;
  eventId?: string;
  eventSeq?: number;
  result: {
    runId: string;
    status: string;
    chatBot?: {
      transcript?: string;
      voiceAffect?: Record<string, unknown>;
      reply?: string;
    };
    languageEmotion?: { transcript?: string };
    wallpaperViews?: FirstVoiceWallpaperViews;
  };
};

/** Analyze the first voice and generate both role-specific wallpaper views. */
export async function generateFirstVoiceWallpapers(
  audioBlob: Blob,
): Promise<FirstVoiceAgentRunResult> {
  const form = new FormData();
  form.append("audio", audioBlob, "first-voice.webm");

  const controller = new AbortController();
  const timeoutMs = 900_000;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(apiUrl("/agent-runs/current/audio"), {
      method: "POST",
      credentials: "include",
      body: form,
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        if (payload?.detail) detail = String(payload.detail);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return (await response.json()) as FirstVoiceAgentRunResult;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("First-voice wallpaper generation timed out");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

/** Update both personalized wallpapers from one speaker's subsequent voice. */
export async function updateCurrentWallpaperFromVoice(
  audioBlob: Blob,
  requestId = crypto.randomUUID(),
): Promise<FirstVoiceAgentRunResult> {
  const form = new FormData();
  form.append("audio", audioBlob, "wallpaper-voice.webm");
  form.append("requestId", requestId);

  const controller = new AbortController();
  const timeoutMs = 900_000;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(
      apiUrl("/agent-runs/current/wallpaper-audio"),
      {
        method: "POST",
        credentials: "include",
        body: form,
        signal: controller.signal,
      },
    );
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        if (payload?.detail) detail = String(payload.detail);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return (await response.json()) as FirstVoiceAgentRunResult;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("Wallpaper update timed out");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

/** Stream subsequent wallpaper voice edits to the 2027 backend. */
export function currentWallpaperVoiceStreamUrl(): string {
  return websocketUrl("/agent-runs/ws/current/wallpaper-audio");
}

export type ComfortReplyResult = {
  runId: string;
  status: string;
  userId: string;
  relationshipId: string;
  comfortReply: {
    text: string;
    suggestedReplies: string[];
    tone: string;
    strategy: string;
    source: Record<string, unknown>;
  };
};

/** Generate a low-pressure reply through the backend ChatBot LLM. */
export async function createComfortReply(
  transcript: string,
  signal?: AbortSignal,
): Promise<ComfortReplyResult> {
  const response = await fetch(apiUrl("/agent-runs/comfort-reply"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcript, persist: false }),
    signal,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) detail = String(payload.detail);
    } catch {
      /* ignore malformed error payloads */
    }
    throw new Error(detail);
  }
  return (await response.json()) as ComfortReplyResult;
}

/** Step 3 — two-pass masked character insertion.
 *
 * Accepts the two portrait photos as File objects (not blob URLs) so
 * the caller does not need to worry about URL.revokeObjectURL().
 *
 * The two portrait files are *always keyed by real age identity*:
 *   - youngerImage = the portrait of the younger person
 *   - elderImage   = the portrait of the elder person
 *
 * The viewerRole field decides which portrait occupies the upper-right
 * "partner" region vs the lower-left "self" region:
 *
 *   viewerRole === "child" → upper-right = elder (partner), lower-left = young (self)
 *   viewerRole === "elder" → upper-right = young (partner), lower-left = elder (self)
 *
 * speechRecognized === false tells the backend to ignore `transcript` and
 * fall back to age-driven default behaviors for both characters.
 *
 * Returns {"imageUrl": "..."} on success.
 * Returns {"imageUrl": ""} if the backend failed; the caller should keep
 * the landscape-only wallpaper and still proceed to WallpaperStage. */
export type InsertCharactersParams = {
  baseImageUrl: string;
  transcript: string;
  youngerImage: File;
  elderImage: File;
  /** "child" | "elder" — current viewer's role from onboarding. */
  viewerRole: "child" | "elder";
  /** True only when ASR produced a non-empty transcript (not a fallback). */
  speechRecognized: boolean;
};

export type InsertCharactersResult = {
  imageUrl: string;
  raw?: unknown;
};

export async function insertCharacters(
  params: InsertCharactersParams,
): Promise<InsertCharactersResult> {
  console.log(
    "[api.insertCharacters] POST /generate-wallpaper/insert-characters start",
  );
  console.log("[api.insertCharacters] baseImageUrl =", params.baseImageUrl);
  console.log("[api.insertCharacters] transcript =", params.transcript);
  console.log("[api.insertCharacters] viewerRole =", params.viewerRole);
  console.log(
    "[api.insertCharacters] speechRecognized =",
    params.speechRecognized,
  );
  console.log(
    "[api.insertCharacters] youngerImage size/type =",
    params.youngerImage.size,
    params.youngerImage.type,
  );
  console.log(
    "[api.insertCharacters] elderImage size/type =",
    params.elderImage.size,
    params.elderImage.type,
  );

  const form = new FormData();
  form.append("baseImageUrl", params.baseImageUrl);
  form.append("transcript", params.transcript);
  // younger_image and elder_image are UploadFile fields on the route.
  form.append("younger_image", params.youngerImage, "younger.jpg");
  form.append("elder_image", params.elderImage, "elder.jpg");
  // Backend reads viewer_role (snake_case) and speech_recognized
  // (snake_case) via FastAPI's File(...) form-field binding.
  form.append("viewer_role", params.viewerRole);
  form.append("speech_recognized", String(params.speechRecognized));

  const controller = new AbortController();
  const timeoutMs = 300_000; // combined two-person edit ≈ up to 5 min
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(apiUrl("/generate-wallpaper/insert-characters"), {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        `insertCharacters timeout after ${Math.round(timeoutMs / 1000)}s`,
      );
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
  window.clearTimeout(timer);

  console.log("[api.insertCharacters] response status =", res.status);

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.text();
      console.error(
        "[api.insertCharacters] backend error body =",
        body.slice(0, 500),
      );
      detail = `insertCharacters failed ${res.status}: ${body.slice(0, 300)}`;
    } catch (err) {
      console.error("[api.insertCharacters] failed to read error body:", err);
    }
    throw new Error(detail);
  }

  const json = (await res.json()) as InsertCharactersResult;
  console.log("[api.insertCharacters] response json =", json);
  console.log("[api.insertCharacters] extracted imageUrl =", json?.imageUrl);
  return json;
}

// ---------------------------------------------------------------------------
// Lift Subject: long-press a subject on the wallpaper to "lift" it out
// with a white outline and float animation (iOS-style).
// ---------------------------------------------------------------------------

export type SubjectRegion = "upper" | "lower";
export type ViewerRole = "child" | "elder";

export type ExtractSubjectParams = {
  imageUrl: string;
  /** Normalized 0..1 x in image natural space. */
  normalizedX: number;
  /** Normalized 0..1 y in image natural space. */
  normalizedY: number;
  region: SubjectRegion;
  viewerRole: ViewerRole;
};

export type SubjectBBox = {
  /** Pixel-space bbox in the image's natural coordinate system. */
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ExtractSubjectResult = {
  cutoutUrl: string;
  bbox: SubjectBBox;
  raw?: unknown;
  /** True if the result was a soft-crop fallback (no real segmentation). */
  fallback?: boolean;
};

/**
 * POST /extract-subject-v2
 *
 * Asks the backend to extract the subject (figure) at the given
 * normalized pointer location and return a transparent PNG cutout
 * with a white outline.
 *
 * The frontend already maps the pointer to a fixed region ("upper"
 * or "lower"); the backend guarantees the cutout stays inside that
 * region (see the matting pipeline + largest-connected-component
 * post-processing in the service).
 *
 * Throws on network/HTTP error. The caller is expected to silently
 * degrade (no UI change) on failure so the rest of the wallpaper
 * flow stays alive.
 */
export async function extractSubject(
  params: ExtractSubjectParams,
): Promise<ExtractSubjectResult> {
  console.log("[api.extractSubject] POST /extract-subject-v2 start");
  console.log("[api.extractSubject] imageUrl =", params.imageUrl);
  console.log("[api.extractSubject] point =", {
    x: params.normalizedX,
    y: params.normalizedY,
  });
  console.log("[api.extractSubject] region =", params.region);
  console.log("[api.extractSubject] viewerRole =", params.viewerRole);

  const controller = new AbortController();
  const timeoutMs = 30_000;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(apiUrl("/extract-subject/extract-subject-v2"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        imageUrl: params.imageUrl,
        viewerRole: params.viewerRole,
        region: params.region,
        pointX: params.normalizedX,
        pointY: params.normalizedY,
      }),
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`extractSubject timed out after ${timeoutMs}ms`);
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
  window.clearTimeout(timer);

  console.log("[api.extractSubject] response status =", res.status);

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const text = await res.text();
      console.error(
        "[api.extractSubject] backend error body =",
        text.slice(0, 500),
      );
      detail = `extractSubject failed ${res.status}: ${text.slice(0, 300)}`;
    } catch (err) {
      console.error("[api.extractSubject] failed to read error body:", err);
    }
    throw new Error(detail);
  }

  const json = (await res.json()) as ExtractSubjectResult;
  console.log("[api.extractSubject] response json =", json);
  console.log(
    "[api.extractSubject] response method =",
    (json.raw as { method?: string } | undefined)?.method,
    "fallback =",
    json.fallback,
  );
  return json;
}

export type EditElderRegionParams = {
  baseImageUrl: string;
  transcript: string;
};

export type EditElderRegionResult = {
  imageUrl: string;
  raw?: unknown;
};

export async function editElderRegion(
  params: EditElderRegionParams,
): Promise<EditElderRegionResult> {
  console.log(
    "[api.editElderRegion] POST /generate-wallpaper/edit-elder-region start",
  );
  console.log("[api.editElderRegion] baseImageUrl =", params.baseImageUrl);
  console.log("[api.editElderRegion] transcript =", params.transcript);

  const body = JSON.stringify({
    baseImageUrl: params.baseImageUrl,
    transcript: params.transcript,
  });

  const controller = new AbortController();
  const timeoutMs = 75_000;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(apiUrl("/generate-wallpaper/edit-elder-region"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: controller.signal,
    });
  } catch (err) {
    window.clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`editElderRegion timed out after ${timeoutMs}ms`);
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
  window.clearTimeout(timer);

  console.log("[api.editElderRegion] response status =", res.status);

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const text = await res.text();
      console.error(
        "[api.editElderRegion] backend error body =",
        text.slice(0, 500),
      );
      detail = `editElderRegion failed ${res.status}: ${text.slice(0, 300)}`;
    } catch (err) {
      console.error("[api.editElderRegion] failed to read error body:", err);
    }
    throw new Error(detail);
  }

  const json = (await res.json()) as EditElderRegionResult;
  console.log("[api.editElderRegion] response json =", json);
  return json;
}

// ---------------------------------------------------------------------------
// Final WallpaperStage: edit the lower-left SELF region (replaces
// editElderRegion).  Behavior is keyed on viewerRole + speechRecognized.
// ---------------------------------------------------------------------------

export type EditSelfRegionParams = {
  baseImageUrl: string;
  /** "child" | "elder" — current viewer's identity. */
  viewerRole: "child" | "elder";
  transcript: string;
  /** True only when ASR produced a non-empty transcript (not a fallback). */
  speechRecognized: boolean;
  /** Optional self portrait File — forwarded to the backend as an identity
   *  reference so the character's face/age/hairstyle is preserved. */
  selfImage?: File;
};

/** Same shape as EditElderRegionResult. */
export type EditSelfRegionResult = EditElderRegionResult;

export async function editSelfRegion(
  params: EditSelfRegionParams,
): Promise<EditSelfRegionResult> {
  console.log("[api.editSelfRegion] start");
  console.log("[api.editSelfRegion] baseImageUrl =", params.baseImageUrl);
  console.log("[api.editSelfRegion] viewerRole =", params.viewerRole);
  console.log(
    "[api.editSelfRegion] speechRecognized =",
    params.speechRecognized,
  );
  console.log("[api.editSelfRegion] has selfImage =", !!params.selfImage);

  const form = new FormData();
  form.append("baseImageUrl", params.baseImageUrl);
  form.append("viewerRole", params.viewerRole);
  form.append("transcript", params.transcript ?? "");
  form.append("speechRecognized", String(params.speechRecognized ?? false));

  if (params.selfImage) {
    form.append("self_image", params.selfImage, "self.jpg");
  }

  const response = await fetch(apiUrl("/generate-wallpaper/edit-self-region"), {
    method: "POST",
    body: form,
    signal: AbortSignal.timeout(300_000),
  });

  const text = await response.text();

  let json: { imageUrl?: string; raw?: unknown; detail?: unknown } = {};
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    json = { rawText: text } as never;
  }

  console.log("[api.editSelfRegion] response status =", response.status);
  console.log("[api.editSelfRegion] response json =", json);
  console.log("[api.editSelfRegion] extracted imageUrl =", json?.imageUrl);

  if (!response.ok) {
    throw new Error(
      `[editSelfRegion] HTTP ${response.status}: ${
        (json as { detail?: unknown })?.detail || text || response.statusText
      }`,
    );
  }

  if (!json.imageUrl) {
    throw new Error("[editSelfRegion] missing imageUrl in response");
  }

  return {
    imageUrl: json.imageUrl,
    raw: json.raw,
  };
}
