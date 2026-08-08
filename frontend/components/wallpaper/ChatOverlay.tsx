"use client";

import { useEffect, useState } from "react";
import {
  formatDayLabel,
  getWallpaperInteractionMode,
  isFirstWallpaper,
  isLatestWallpaper,
  useSceneStore,
} from "@/lib/hooks/useSceneStore";
import type { Message } from "@/lib/hooks/useSceneStore";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { useWallpaperVoiceEditRecorder } from "@/lib/hooks/useWallpaperVoiceEditRecorder";
import { useI18n, formatLongDate, formatSummaryDate } from "@/lib/i18n";
import {
  createComfortReply,
  getCurrentRelationshipCharacterAssets,
  recordExperimentEvent,
  resolveApiAssetUrl,
  type CharacterAsset,
  type RelationshipSummary,
} from "@/lib/api";
import { subscribeToCharacterAssetEvents } from "@/lib/characterAssetEvents";

export type CommunicationSummary = {
  stateOneLiner: string;
  visualClues: string;
  suggestedReplies: string[];
  suggestedAction: string;
  avoid: string;
};

export const SUMMARY_PROMPT_TEMPLATE = `...`;

function backendPortraitUrl(asset: CharacterAsset | null): string {
  if (!asset) return "";
  const path =
    asset.portraitImageUrl ||
    asset.masterImageUrl ||
    asset.halfBodyImageUrl ||
    asset.sourceImageUrl;
  return path ? resolveApiAssetUrl(path) : "";
}

/**
 * Build a CommunicationSummary from the real backend `relationshipSummary`
 * carried by the currently-selected wallpaper revision. Returns `null`
 * when no real summary is available so the UI can render an empty state
 * instead of fabricating a fake one.
 *
 * The 2027 backend contract:
 *   - `themeZh` / `themeEn`        → suggestedReplies[0] (one-line theme)
 *   - `descriptionZh` / `descriptionEn` → stateOneLiner  (long summary)
 *   - `spatialMode`                         → visualClues (visual context)
 *
 * The `suggestedAction` and `avoid` fields are not provided by the
 * backend; when real LLM replies are computed, callers should layer
 * those onto the returned summary via `displaySummary`.
 */
function buildCommunicationSummary(
  relationshipSummary: RelationshipSummary | undefined,
  language: "en" | "zh",
): CommunicationSummary | null {
  if (!relationshipSummary) return null;
  const theme =
    (language === "zh"
      ? relationshipSummary.themeZh
      : relationshipSummary.themeEn) || "";
  const description =
    (language === "zh"
      ? relationshipSummary.descriptionZh
      : relationshipSummary.descriptionEn) || "";
  const visualClues = relationshipSummary.spatialMode || "";
  if (!theme && !description && !visualClues) return null;
  return {
    stateOneLiner: description || theme,
    visualClues,
    suggestedReplies: theme ? [theme] : [],
    suggestedAction: "",
    avoid: "",
  };
}

function formatTime(timestamp: number): string {
  const d = new Date(timestamp);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

// ── Date Header ───────────────────────────────────────────────────────────────

function DateHeader({
  dayIndex,
  locale,
}: {
  dayIndex: number;
  locale: string;
}) {
  const { t } = useI18n();
  const formatted = formatLongDate(dayIndex, locale);
  return (
    <div className="shrink-0 text-center">
      <h1
        className="text-[38px] font-extrabold tracking-[-0.025em] text-black"
        style={{ lineHeight: 1.2 }}
      >
        {formatted}
      </h1>
    </div>
  );
}

// ── Mood Summary Card ─────────────────────────────────────────────────────────

function MoodFlower() {
  const petals = Array.from({ length: 12 }, (_, index) => index * 30);

  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 180 180"
      className="h-[150px] w-[150px] shrink-0 drop-shadow-[0_12px_18px_rgba(74,35,145,0.22)]"
    >
      <defs>
        <radialGradient id="moodFlowerCore" cx="50%" cy="45%" r="58%">
          <stop offset="0%" stopColor="#39205f" />
          <stop offset="42%" stopColor="#7042aa" />
          <stop offset="100%" stopColor="#b59ee7" stopOpacity="0.28" />
        </radialGradient>
        <linearGradient id="moodFlowerPetal" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f0eaff" stopOpacity="0.92" />
          <stop offset="55%" stopColor="#b9a5eb" stopOpacity="0.7" />
          <stop offset="100%" stopColor="#7452bb" stopOpacity="0.42" />
        </linearGradient>
      </defs>
      {petals.map((rotation) => (
        <path
          key={`outer-${rotation}`}
          d="M90 88 C64 62 67 24 90 8 C113 25 116 62 90 88Z"
          fill="url(#moodFlowerPetal)"
          stroke="#8d6bd0"
          strokeWidth="1.2"
          transform={`rotate(${rotation} 90 90)`}
        />
      ))}
      {petals.slice(0, 8).map((_, index) => (
        <path
          key={`inner-${index}`}
          d="M90 92 C72 74 74 45 90 31 C107 46 109 74 90 92Z"
          fill={index % 2 === 0 ? "#8260c5" : "#a88ddd"}
          fillOpacity="0.76"
          transform={`rotate(${index * 45 + 22.5} 90 90)`}
        />
      ))}
      <circle cx="90" cy="90" r="34" fill="url(#moodFlowerCore)" />
      <circle cx="90" cy="90" r="6" fill="#d8c9ff" />
    </svg>
  );
}

function MoodSummaryCard({ summary }: { summary: CommunicationSummary }) {
  return (
    <section className="mt-5 shrink-0 rounded-[38px] border border-white/80 bg-white/55 px-5 py-7 shadow-[0_14px_28px_rgba(78,54,90,0.18)] backdrop-blur-xl">
      <div className="flex items-center gap-5">
        <MoodFlower />
        <div className="min-w-0 flex-1 text-center">
          <h2 className="text-[31px] font-extrabold leading-[1.22] tracking-[-0.015em] text-black">
            {summary.stateOneLiner}
          </h2>
          <p className="mt-5 line-clamp-3 text-[21px] font-medium leading-[1.45] text-[#918b92]">
            {summary.visualClues}
          </p>
        </div>
      </div>
    </section>
  );
}

// ── AI Suggestions ────────────────────────────────────────────────────────────

function AISuggestionsSection({
  summary,
  language,
}: {
  summary: CommunicationSummary;
  language: "en" | "zh";
}) {
  const { t } = useI18n();
  return (
    <section
      className="mt-6 shrink-0 rounded-[38px] border border-white/80 bg-white/55 px-6 py-6 backdrop-blur-xl"
      style={{
        boxShadow:
          "0 14px 28px rgba(78,54,90,0.16), 0 1px 2px rgba(255,255,255,0.7) inset",
      }}
    >
      {/* Section title */}
      <div className="flex items-center gap-2.5">
        <span className="text-[25px] text-[#ff6b2d]" aria-hidden>
          ✦
        </span>
        <h2
          style={{
            fontSize: 28,
            fontWeight: 800,
            color: "#1F2937",
            letterSpacing: "0.005em",
          }}
        >
          {t("chat.aiSuggestions")}
        </h2>
        <span aria-hidden className="text-lg" style={{ color: "#FFB020" }}>
          ✨
        </span>
      </div>

      {/* Reply cards */}
      <div className="mt-5 flex flex-col gap-3">
        {summary.suggestedReplies.map((reply, i) => (
          <div
            key={i}
            className="flex items-start gap-3 rounded-[14px] bg-white/90 px-4 py-3 shadow-[0_4px_10px_rgba(72,55,82,0.08)]"
            style={{
              border: "1px solid rgba(255,255,255,0.9)",
            }}
          >
            <span
              className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
              style={{
                background: "linear-gradient(135deg, #C179F2 0%, #FF9A22 100%)",
                boxShadow: "0 2px 6px rgba(193,121,242,0.28)",
              }}
            >
              {i + 1}
            </span>
            <span
              className="flex-1 text-slate-700"
              style={{
                fontSize: 17,
                lineHeight: 1.5,
                fontWeight: 500,
                letterSpacing: "0.01em",
              }}
            >
              {reply}
            </span>
          </div>
        ))}
      </div>

      {/* Tips */}
      <div
        className="mt-4 flex items-start gap-2.5 rounded-[14px] bg-white/85 px-4 py-3 shadow-[0_4px_10px_rgba(72,55,82,0.06)]"
        style={{
          border: "1px solid rgba(255,255,255,0.9)",
        }}
      >
        <span
          className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md"
          style={{ background: "rgba(193,121,242,0.14)" }}
          aria-hidden
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="#7A3FB8"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-3 w-3"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </span>
        <div>
          <p
            className="text-slate-600"
            style={{
              fontSize: 16,
              lineHeight: 1.65,
              fontWeight: 500,
              marginBottom: 6,
            }}
          >
            <span className="text-slate-700" style={{ fontWeight: 600 }}>
              {t("chat.touchSuggestion")}
            </span>{" "}
            {summary.suggestedAction}
          </p>
          <p
            className="text-slate-500"
            style={{
              fontSize: 15,
              lineHeight: 1.65,
            }}
          >
            <span className="text-slate-600" style={{ fontWeight: 600 }}>
              {t("chat.note")}
            </span>{" "}
            {summary.avoid}
          </p>
        </div>
      </div>
    </section>
  );
}

// ── Original Chat History ─────────────────────────────────────────────────────

function ChatBubble({
  msg,
  isFirst,
  isLast,
  voiceLabel,
}: {
  msg: Message;
  isFirst: boolean;
  isLast: boolean;
  voiceLabel: string;
}) {
  const isElder = msg.from === "elder";
  const bubbleBg = isElder
    ? "rgba(244,236,249,0.96)"
    : "rgba(255,255,255,0.94)";
  const align = isElder ? "items-start" : "items-end";
  const mlAuto = !isElder;

  return (
    <div className={`flex flex-col ${align} gap-1.5`}>
      {/* Time */}
      <div
        className={`text-center text-[16px] font-medium ${isElder ? "text-orange-400/80" : "text-purple-400/80"}`}
        style={{ paddingLeft: isElder ? 4 : 0, paddingRight: isElder ? 0 : 4 }}
      >
        {formatTime(msg.timestamp)}
      </div>
      {/* Bubble */}
      <div
        className={[
          "max-w-[82%] rounded-[18px] px-4 py-3 text-[#28242c]",
          mlAuto ? "ml-auto" : "",
        ].join(" ")}
        style={{
          background: bubbleBg,
          boxShadow: "0 7px 15px rgba(77,55,87,0.09)",
          fontSize: 18,
          lineHeight: 1.55,
          fontWeight: 500,
          letterSpacing: "0.01em",
        }}
      >
        {msg.type === "voice_message" && (
          <div className="mb-1.5 flex items-center gap-1.5">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="#7A3FB8"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-3.5 w-3.5 flex-shrink-0"
            >
              <path d="M12 1a3 3 0 0 0-3 4v7a3 3 0 0 0 6 0v-7a3 3 0 0 0-3-4z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
            </svg>
            <span className="text-[14px] font-semibold text-slate-500">
              {Math.round(msg.durationSec)}s {voiceLabel}
            </span>
          </div>
        )}
        {msg.text}
      </div>
    </div>
  );
}

function OriginalChatHistorySection({
  messages,
  dayIndex,
  language,
}: {
  messages: Message[];
  dayIndex: number;
  language: "en" | "zh";
}) {
  const { t } = useI18n();
  const displayMessages = messages;

  return (
    <section
      className="mt-6 flex min-h-0 flex-1 flex-col rounded-[38px] border border-white/80 bg-white/55 px-6 py-6 backdrop-blur-xl"
      style={{
        boxShadow:
          "0 14px 28px rgba(78,54,90,0.16), 0 1px 2px rgba(255,255,255,0.7) inset",
      }}
    >
      {/* Section title */}
      <div className="mb-4 flex shrink-0 items-center justify-center gap-0">
        <h2
          style={{
            fontSize: 28,
            fontWeight: 800,
            color: "#1F2937",
            letterSpacing: "0.005em",
          }}
        >
          {t("chat.originalHistory")}
        </h2>
      </div>

      {/* Scrollable messages */}
      <div
        className="summary-chat-scroll min-h-0 flex-1 overflow-y-auto pr-1"
        style={{ scrollbarWidth: "thin" }}
      >
        {displayMessages.length === 0 ? (
          <div
            className="flex h-full items-center justify-center"
            style={{ paddingTop: 16, paddingBottom: 16 }}
          >
            <p
              className="text-center"
              style={{ fontSize: 16, color: "#9CA3AF", lineHeight: 1.6 }}
            >
              {t("chat.noHistory")}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4 pb-2">
            {displayMessages.map((msg, i) => (
              <ChatBubble
                key={msg.id}
                msg={msg}
                isFirst={i === 0}
                isLast={i === displayMessages.length - 1}
                voiceLabel={t("chat.voiceSec", {
                  n: Math.round((msg as { durationSec: number }).durationSec),
                })}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function SummaryAvatar({
  imageUrl,
  label,
}: {
  imageUrl: string | null;
  label: string;
}) {
  return (
    <span className="relative flex h-[clamp(48px,9vw,68px)] w-[clamp(48px,9vw,68px)] shrink-0 items-center justify-center overflow-hidden rounded-full border-[3px] border-[#66513f] bg-[#efe5d8] shadow-sm">
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={label}
          className="h-full w-full object-cover"
        />
      ) : (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-7 w-7 text-[#786552]"
        >
          <circle cx="12" cy="8" r="4" fill="currentColor" />
          <path d="M4.5 21a7.5 7.5 0 0 1 15 0" fill="currentColor" />
        </svg>
      )}
    </span>
  );
}

function ConnectionSummaryCard({
  summary,
  relationshipTheme,
  relationshipDescription,
  childPortraitUrl,
  elderPortraitUrl,
  language,
}: {
  summary: CommunicationSummary | null;
  relationshipTheme: string;
  relationshipDescription: string;
  childPortraitUrl: string | null;
  elderPortraitUrl: string | null;
  language: "en" | "zh";
}) {
  const { t } = useI18n();
  return (
    <section className="mt-[clamp(18px,2.4vh,28px)] shrink-0 rounded-[32px] border-2 border-white/[0.85] bg-[#d8b191]/90 px-[clamp(20px,5vw,34px)] py-[clamp(20px,2.8vh,32px)] text-center shadow-[0_12px_22px_rgba(74,57,43,0.20)] backdrop-blur-sm">
      <h2 className="text-[clamp(26px,4.8vw,36px)] font-extrabold leading-tight tracking-[-0.025em] text-black">
        <span aria-hidden="true" className="mr-2">
          ✦
        </span>
        {t("chat.todaysConnection")}
      </h2>

      <div className="mt-2 flex items-center justify-center gap-[clamp(12px,3vw,22px)]">
        <SummaryAvatar
          imageUrl={childPortraitUrl}
          label={t("chat.adultChild")}
        />
        <p className="min-w-0 text-[clamp(38px,8.2vw,62px)] font-extrabold leading-none tracking-[-0.055em] text-white">
          {relationshipTheme || t("chat.hope")}
        </p>
        <SummaryAvatar
          imageUrl={elderPortraitUrl}
          label={t("chat.olderAdult")}
        />
      </div>

      <p className="mx-auto mt-4 line-clamp-2 max-w-[520px] text-[clamp(22px,3.8vw,29px)] font-semibold leading-[1.35] text-white">
        {relationshipDescription || summary?.stateOneLiner || ""}
      </p>
    </section>
  );
}

function SummaryAbstractCard({
  summary,
}: {
  summary: CommunicationSummary | null;
}) {
  const { t } = useI18n();
  const suggestions = (summary?.suggestedReplies ?? [])
    .filter(Boolean)
    .slice(0, 2);
  return (
    <section className="mt-[clamp(16px,2.2vh,24px)] shrink-0 rounded-[32px] border border-white/90 bg-white/[0.82] px-[clamp(22px,5vw,34px)] py-[clamp(18px,2.4vh,28px)] text-center shadow-[0_10px_20px_rgba(69,58,48,0.16)] backdrop-blur-md">
      <h2 className="flex items-center justify-center gap-2 text-[clamp(27px,5vw,37px)] font-extrabold leading-tight tracking-[-0.025em] text-black">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          className="h-7 w-7 shrink-0"
        >
          <rect
            x="4"
            y="3"
            width="16"
            height="18"
            rx="1.5"
            stroke="currentColor"
            strokeWidth="2"
          />
          <path
            d="M8 8h8M8 12h8M8 16h6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
        {t("chat.aiSuggestions")}
      </h2>

      {suggestions.length === 0 ? (
        <p className="mt-4 text-center text-[clamp(18px,3vw,22px)] font-semibold leading-snug text-black/[0.45]">
          {t("chat.noHistory")}
        </p>
      ) : (
        <div className="mt-4 grid gap-3">
          {suggestions.map((suggestion, index) => (
            <div
              key={`${index}-${suggestion}`}
              className="flex items-start gap-3 rounded-[18px] bg-[#f5efe7]/90 px-4 py-3.5 text-left"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#d8b191] text-[17px] font-extrabold leading-none text-white">
                {index + 1}
              </span>
              <p className="min-w-0 flex-1 text-[clamp(19px,3.2vw,25px)] font-semibold leading-[1.4] text-[#302b27] [text-wrap:pretty]">
                {suggestion}
              </p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── Chat history avatar ───────────────────────────────────────────────────────

function ChatHistoryAvatar({
  imageUrl,
  label,
}: {
  imageUrl: string | null;
  label: string;
}) {
  return (
    <span className="relative h-[46px] w-[46px] shrink-0 overflow-hidden rounded-full border-2 border-white bg-[#e5ddd3] shadow-sm">
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={label}
          className="h-full w-full object-cover"
        />
      ) : (
        <span className="absolute inset-0 bg-black/10" />
      )}
    </span>
  );
}

// ── Chat message preview ──────────────────────────────────────────────────────

function SummaryChatPreview({
  messages,
  dayIndex,
  language,
  viewerRole,
  selfPortraitUrl,
  partnerPortraitUrl,
}: {
  messages: Message[];
  dayIndex: number;
  language: "en" | "zh";
  viewerRole: "elder" | "child";
  selfPortraitUrl: string | null;
  partnerPortraitUrl: string | null;
}) {
  const { t } = useI18n();
  const displayMessages = messages.slice(-4);

  return (
    <section className="mt-[clamp(16px,2.2vh,24px)] flex min-h-[220px] flex-1 flex-col overflow-hidden rounded-[32px] border border-white/90 bg-white/[0.82] px-[clamp(20px,4.5vw,32px)] py-[clamp(18px,2.4vh,28px)] shadow-[0_10px_20px_rgba(69,58,48,0.16)] backdrop-blur-md">
      <h2 className="flex shrink-0 items-center justify-center gap-2 text-[clamp(27px,5vw,37px)] font-extrabold leading-tight tracking-[-0.025em] text-black">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          className="h-8 w-8 shrink-0"
        >
          <path
            d="M21 15a3 3 0 0 1-3 3H9l-5 3v-6a7 7 0 1 1 17 0Z"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {t("chat.originalHistory")}
      </h2>

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto pr-2 [scrollbar-width:thin]">
        {displayMessages.length === 0 ? (
          <div className="flex h-full min-h-[130px] items-center justify-center">
            <p className="text-center text-[clamp(22px,3.8vw,28px)] font-semibold leading-snug text-black/[0.45]">
              {t("chat.noHistory")}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4 pb-2">
            {displayMessages.map((message) => {
              const isSelf = message.from === viewerRole;
              const avatarUrl = isSelf ? selfPortraitUrl : partnerPortraitUrl;
              const avatarLabel = isSelf
                ? language === "zh"
                  ? "自己"
                  : "Self"
                : language === "zh"
                  ? "对方"
                  : "Partner";
              return (
                <div key={message.id} className="flex flex-col">
                  <time className="mb-1 text-center text-[clamp(17px,2.8vw,21px)] font-semibold text-black/[0.38]">
                    {formatTime(message.timestamp)}
                  </time>
                  <div
                    className={[
                      "flex items-end gap-2",
                      isSelf ? "flex-row" : "flex-row-reverse",
                    ].join(" ")}
                  >
                    <ChatHistoryAvatar
                      imageUrl={avatarUrl}
                      label={avatarLabel}
                    />
                    <div
                      className={[
                        "max-w-[76%] rounded-[20px] px-5 py-4 text-[clamp(20px,3.5vw,27px)] font-semibold leading-[1.4] text-[#302b27]",
                        isSelf ? "bg-white/95" : "bg-[#ece8e4]/95",
                      ].join(" ")}
                    >
                      <p className="line-clamp-3">{message.text}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

// ── ChatOverlay ──────────────────────────────────────────────────────────────

export function ChatOverlay() {
  const { t, language, locale } = useI18n();
  const uiMode = useSceneStore((s) => s.uiMode);
  const toggleUiMode = useSceneStore((s) => s.toggleUiMode);
  const currentDayIndex = useSceneStore((s) => s.currentDayIndex);
  const currentWallpaperIndex = useSceneStore((s) => s.currentWallpaperIndex);
  const currentDayWallpapers = useSceneStore(
    (s) =>
      s.wallpapersByDay[(["前天", "昨天", "今天"] as const)[s.currentDayIndex]],
  );
  const shiftWallpaper = useSceneStore((s) => s.shiftWallpaper);
  const isFirst = useSceneStore(isFirstWallpaper);
  const isLatest = useSceneStore(isLatestWallpaper);
  const initialInsertStatus = useSceneStore((s) => s.initialInsertStatus);
  const generatedWallpaperUrl = useSceneStore((s) => s.generatedWallpaperUrl);
  const messagesByDay = useSceneStore((s) => s.messagesByDay);

  const role = useOnboardingStore((s) => s.role);
  const selfPhotoUrl = useOnboardingStore((s) => s.selfPhotoUrl);
  const partnerPhotoUrl = useOnboardingStore((s) => s.partnerPhotoUrl);
  const viewerRole = role === "elder" ? "elder" : "child";

  const {
    status: voiceStatus,
    elapsedSec,
    toggle,
  } = useWallpaperVoiceEditRecorder();
  const [hint, setHint] = useState<string | null>(null);
  const [llmReplies, setLlmReplies] = useState<Record<string, string[]>>({});
  const [replyLoadingKey, setReplyLoadingKey] = useState<string | null>(null);
  const [replyErrorKeys, setReplyErrorKeys] = useState<Record<string, true>>(
    {},
  );
  const [backendPortraits, setBackendPortraits] = useState({
    child: "",
    elder: "",
  });

  useEffect(() => {
    let stopped = false;

    const refreshPortraits = async () => {
      try {
        const assets = await getCurrentRelationshipCharacterAssets();
        if (stopped) return;
        setBackendPortraits({
          child: backendPortraitUrl(assets.child),
          elder: backendPortraitUrl(assets.elder),
        });
      } catch (error) {
        console.debug("[ChatOverlay] portrait refresh skipped", error);
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void refreshPortraits();
    };

    void refreshPortraits();
    const unsubscribe = subscribeToCharacterAssetEvents(refreshPortraits);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      stopped = true;
      unsubscribe();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  // Central button shows ONLY for initial_voice (first voice pending)
  // and processing (first voice in progress). All other modes keep the
  // button out of the DOM — including loading (pre-sync) and
  // history_disabled (past day or older revision).
  const interactionMode = useSceneStore(getWallpaperInteractionMode);
  const showCentralRecordingButton =
    interactionMode === "initial_voice" || interactionMode === "processing";

  useEffect(() => {
    let msg: string | null = null;
    switch (voiceStatus) {
      case "transcribing":
        msg = t("chat.transcribing");
        break;
      case "editing":
        msg = t("chat.generating");
        break;
      default:
        msg = null;
    }
    setHint(msg);
    if (!msg) return;
    const id = setTimeout(() => setHint(null), 4000);
    return () => clearTimeout(id);
  }, [voiceStatus, t]);

  const isWhite = uiMode === "white";
  const dayKey = (["前天", "昨天", "今天"] as const)[currentDayIndex];
  const todayMessages = messagesByDay[dayKey] ?? [];
  const replyContextMessages = todayMessages;
  const replyTarget =
    [...replyContextMessages]
      .reverse()
      .find((message) => message.from !== viewerRole) ??
    replyContextMessages.at(-1);
  const replyRequestKey = replyTarget
    ? `${dayKey}:${replyTarget.id}:${replyTarget.text}`
    : "";
  const persistedChatbotReplies = Array.from(
    new Set(
      (replyTarget?.suggestedReplies?.length
        ? replyTarget.suggestedReplies
        : [replyTarget?.suggestedReply || ""]
      )
        .map((reply) => reply.trim())
        .filter(Boolean),
    ),
  ).slice(0, 2);
  const llmReplySuggestions = Array.from(
    new Set([
      ...persistedChatbotReplies,
      ...(replyRequestKey ? llmReplies[replyRequestKey] || [] : []),
    ]),
  ).slice(0, 2);
  const selectedWallpaperUrl =
    currentDayWallpapers[currentWallpaperIndex]?.imageUrl ||
    generatedWallpaperUrl ||
    "";
  const relationshipSummary =
    currentDayWallpapers[currentWallpaperIndex]?.relationshipSummary ||
    replyTarget?.relationshipSummary;
  // Build the summary from the real backend relationshipSummary carried
  // by the current wallpaper item. Returns null when no real summary is
  // available — the UI renders an empty state in that case (no fake
  // placeholder text).
  const baseSummary = buildCommunicationSummary(relationshipSummary, language);
  const displaySummary: CommunicationSummary | null =
    llmReplySuggestions.length > 0
      ? {
          stateOneLiner:
            baseSummary?.stateOneLiner ||
            (language === "zh" ? "暂无总结" : "No summary yet"),
          visualClues: baseSummary?.visualClues || "",
          suggestedReplies:
            replyLoadingKey === replyRequestKey &&
            llmReplySuggestions.length < 2
              ? [
                  ...llmReplySuggestions,
                  language === "zh"
                    ? "正在生成第二条建议…"
                    : "Generating another reply…",
                ]
              : llmReplySuggestions,
          suggestedAction: baseSummary?.suggestedAction || "",
          avoid: baseSummary?.avoid || "",
        }
      : replyLoadingKey === replyRequestKey && replyRequestKey
        ? {
            stateOneLiner:
              baseSummary?.stateOneLiner ||
              (language === "zh" ? "正在生成回复建议…" : "Generating a reply…"),
            visualClues: baseSummary?.visualClues || "",
            suggestedReplies: [
              language === "zh" ? "正在生成回复建议…" : "Generating a reply…",
            ],
            suggestedAction: baseSummary?.suggestedAction || "",
            avoid: baseSummary?.avoid || "",
          }
        : baseSummary;
  const relationshipTheme =
    (language === "zh"
      ? relationshipSummary?.themeZh
      : relationshipSummary?.themeEn) || t("chat.hope");
  const relationshipDescription =
    (language === "zh"
      ? relationshipSummary?.descriptionZh
      : relationshipSummary?.descriptionEn) ||
    baseSummary?.stateOneLiner ||
    displaySummary?.stateOneLiner ||
    (language === "zh" ? "暂无描述" : "No description yet");

  useEffect(() => {
    if (
      !isWhite ||
      !replyTarget?.text.trim() ||
      !replyRequestKey ||
      persistedChatbotReplies.length >= 2 ||
      (llmReplies[replyRequestKey]?.length || 0) >= 2 ||
      replyErrorKeys[replyRequestKey]
    ) {
      return;
    }

    const controller = new AbortController();
    setReplyLoadingKey(replyRequestKey);
    void createComfortReply(replyTarget.text.trim(), controller.signal)
      .then((response) => {
        const replies = Array.from(
          new Set(
            [
              ...persistedChatbotReplies,
              ...(response.comfortReply.suggestedReplies || []),
              response.comfortReply.text,
            ]
              .map((reply) => reply.trim())
              .filter(Boolean),
          ),
        ).slice(0, 2);
        if (!replies.length) throw new Error("ChatBot returned an empty reply");
        setLlmReplies((current) => ({
          ...current,
          [replyRequestKey]: Array.from(
            new Set([...(current[replyRequestKey] || []), ...replies]),
          ).slice(0, 2),
        }));
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError")
          return;
        console.error("[ChatOverlay] comfort reply failed", error);
        setReplyErrorKeys((current) => ({
          ...current,
          [replyRequestKey]: true,
        }));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setReplyLoadingKey((current) =>
            current === replyRequestKey ? null : current,
          );
        }
      });

    return () => controller.abort();
  }, [
    isWhite,
    llmReplies,
    replyErrorKeys,
    replyRequestKey,
    replyTarget?.text,
    persistedChatbotReplies.join("\u0000"),
  ]);

  const handleDownloadWallpaper = async () => {
    if (!selectedWallpaperUrl) return;

    const selectedWallpaper = currentDayWallpapers[currentWallpaperIndex];
    void recordExperimentEvent({
      eventName: "wallpaper.download_requested",
      sceneId: selectedWallpaper?.revisionId || "",
      updateId:
        selectedWallpaper?.eventSeq === undefined
          ? ""
          : String(selectedWallpaper.eventSeq),
      payload: {
        imageUrl: selectedWallpaperUrl,
        dayKey,
        isDemo: Boolean(selectedWallpaper?.isDemo),
      },
    }).catch((error) => {
      console.warn("[ChatOverlay] download event logging failed", error);
    });

    try {
      const response = await fetch(selectedWallpaperUrl);
      if (!response.ok) {
        throw new Error(`Download failed: ${response.status}`);
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const extension = blob.type.includes("jpeg") ? "jpg" : "png";
      const anchor = document.createElement("a");

      anchor.href = objectUrl;
      anchor.download = `time-wallpaper-${Date.now()}.${extension}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      console.error("[ChatOverlay] wallpaper download failed", error);

      const anchor = document.createElement("a");
      anchor.href = selectedWallpaperUrl;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.click();
    }
  };

  const childPortraitUrl =
    backendPortraits.child ||
    (role === "elder" ? partnerPhotoUrl : selfPhotoUrl);
  const elderPortraitUrl =
    backendPortraits.elder ||
    (role === "elder" ? selfPhotoUrl : partnerPhotoUrl);

  // ── AI Summary page (back side) ──────────────────────────────────────
  if (isWhite) {
    return (
      <div className="absolute inset-0 z-[80] overflow-hidden bg-[#f3ede2] text-black">
        {selectedWallpaperUrl ? (
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-cover bg-center bg-no-repeat"
            style={{
              backgroundImage: `url(${selectedWallpaperUrl})`,
              filter: "saturate(0.72) brightness(1.12)",
              opacity: 0.24,
              transform: "scale(1.035)",
            }}
          />
        ) : null}
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-[rgba(250,246,238,0.34)]"
        />

        {/* Download wallpaper button */}
        <button
          type="button"
          aria-label={language === "zh" ? "下载壁纸" : "Download wallpaper"}
          title={language === "zh" ? "下载壁纸" : "Download wallpaper"}
          disabled={!selectedWallpaperUrl}
          onClick={(event) => {
            event.stopPropagation();
            void handleDownloadWallpaper();
          }}
          className="pointer-events-auto absolute left-[clamp(16px,3.5vw,28px)] top-[clamp(16px,2.5vh,28px)] z-[100] flex h-[56px] w-[56px] items-center justify-center rounded-full bg-white/[0.82] text-black shadow-[0_7px_16px_rgba(58,48,39,0.15)] backdrop-blur-md transition active:translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-7 w-7"
          >
            <path d="M12 3v12" />
            <path d="m7 10 5 5 5-5" />
            <path d="M5 21h14" />
          </svg>
        </button>

        <button
          type="button"
          aria-label={t("chat.backToWallpaper")}
          onClick={(e) => {
            e.stopPropagation();
            toggleUiMode();
          }}
          className="pointer-events-auto absolute right-[clamp(16px,3.5vw,28px)] top-[clamp(16px,2.5vh,28px)] z-[100] flex h-[56px] w-[56px] items-center justify-center rounded-full bg-white/[0.82] text-black shadow-[0_7px_16px_rgba(58,48,39,0.15)] backdrop-blur-md transition hover:-translate-x-0.5 active:translate-x-0"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-7 w-7"
            aria-hidden
          >
            <path d="M19 12H5" />
            <path d="M12 19l-7-7 7-7" />
          </svg>
        </button>

        <div className="relative z-10 flex h-full flex-col overflow-hidden px-[clamp(20px,4.5vw,34px)] pb-[clamp(18px,2.8vh,30px)] pt-[clamp(30px,4.2vh,52px)]">
          <header className="shrink-0 px-[58px] text-center">
            <h1 className="text-[clamp(34px,6.2vw,48px)] font-extrabold leading-tight tracking-[-0.035em] text-[#4d4135]">
              {formatSummaryDate(currentDayIndex, locale)}
            </h1>
            <div className="mx-auto mt-3 h-[4px] w-[82%] rounded-full bg-black/60" />
          </header>

          <div className="flex min-h-0 flex-1 flex-col">
            <ConnectionSummaryCard
              summary={displaySummary}
              relationshipTheme={relationshipTheme}
              relationshipDescription={relationshipDescription}
              childPortraitUrl={childPortraitUrl}
              elderPortraitUrl={elderPortraitUrl}
              language={language}
            />
            <SummaryAbstractCard summary={displaySummary} />
            <SummaryChatPreview
              messages={todayMessages}
              dayIndex={currentDayIndex}
              language={language}
              viewerRole={viewerRole}
              selfPortraitUrl={
                viewerRole === "child" ? childPortraitUrl : elderPortraitUrl
              }
              partnerPortraitUrl={
                viewerRole === "child" ? elderPortraitUrl : childPortraitUrl
              }
            />
          </div>
        </div>
      </div>
    );
  }

  // ── Wallpaper front side ──────────────────────────────────────────────
  return (
    <>
      {hint ? (
        <div className="pointer-events-none absolute bottom-12 left-1/2 -translate-x-1/2 rounded-2xl bg-black/60 px-4 py-2 text-sm text-white backdrop-blur">
          {hint}
        </div>
      ) : null}

      {/* Wallpaper history navigation buttons */}
      {voiceStatus === "idle" && (
        <>
          <button
            className="absolute left-0 top-16 bottom-16 z-50 w-16 bg-transparent"
            onClick={() => shiftWallpaper(-1)}
            disabled={isFirst}
            aria-label={t("chat.prevWallpaper")}
          />
          <button
            className="absolute right-0 top-16 bottom-16 z-50 w-16 bg-transparent"
            onClick={() => shiftWallpaper(1)}
            disabled={isLatest}
            aria-label={t("chat.nextWallpaper")}
          />
        </>
      )}

      {/* Top-right flip toggle */}
      <button
        onClick={toggleUiMode}
        className="absolute right-0 top-0 z-50 h-20 w-20 bg-transparent"
        aria-label={
          isWhite ? t("chat.backToWallpaper") : t("chat.openSuggestions")
        }
      />

      {/* Floating date pill — hidden on today's latest wallpaper where the
          lock-screen clock is shown instead. Historical days keep the pill. */}
      {!isLatest ? (
        <div
          className="pointer-events-none absolute top-4 left-1/2 z-50 flex select-none items-center rounded-full bg-black/30 px-4 py-2 shadow-md backdrop-blur-sm"
          style={{ transform: "translateX(-50%)" }}
        >
          <span className="min-w-[2.5rem] text-center text-sm font-semibold text-white">
            {formatDayLabel(currentDayIndex)}
          </span>
          {currentDayWallpapers.length > 1 ? (
            <span className="ml-2 text-xs font-medium text-white/80">
              {currentWallpaperIndex + 1}/{currentDayWallpapers.length}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Central recording button — visible only during initial_voice / processing */}
      {showCentralRecordingButton ? (
        <button
          type="button"
          onClick={() => toggle(viewerRole)}
          disabled={voiceStatus === "transcribing" || voiceStatus === "editing"}
          aria-label={
            voiceStatus === "recording"
              ? t("chat.stopRecording")
              : t("chat.startRecording")
          }
          className={[
            "absolute left-1/2 top-1/2 z-[70] flex -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-full",
            // Size: 80px — large enough for elderly users
            "h-20 w-20 shrink-0",
            // Appearance
            "bg-black/40 shadow-[0_4px_20px_rgba(0,0,0,0.28)] backdrop-blur-md",
            // States
            voiceStatus === "recording"
              ? "ring-2 ring-white/60 animate-pulse"
              : voiceStatus === "transcribing" || voiceStatus === "editing"
                ? "opacity-60 cursor-not-allowed"
                : "cursor-pointer hover:bg-black/50 active:scale-95 transition-all duration-150",
          ].join(" ")}
        >
          {voiceStatus === "recording" ? (
            // Stop icon
            <div className="h-7 w-7 rounded-sm bg-white/90 shadow-sm" />
          ) : voiceStatus === "transcribing" || voiceStatus === "editing" ? (
            // Loading spinner
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className="h-8 w-8 animate-spin text-white"
              aria-hidden="true"
            >
              <circle
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="3"
                strokeDasharray="40 20"
                strokeLinecap="round"
              />
            </svg>
          ) : (
            // Microphone icon
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className="h-9 w-9 text-white"
              aria-hidden="true"
            >
              <path
                d="M12 2a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4Z"
                fill="currentColor"
                stroke="none"
              />
              <path
                d="M19 10v1a7 7 0 0 1-14 0v-1M12 19v3M8 22h8"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
          {/* Elapsed seconds — shown only while recording */}
          {voiceStatus === "recording" && elapsedSec > 0 ? (
            <span className="mt-1 text-xs font-medium text-white/80">
              {elapsedSec}s
            </span>
          ) : null}
        </button>
      ) : null}
    </>
  );
}
