"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";

type DateParts = {
  dateLine: string;
  timeLine: string;
};

function formatClock(locale: string): DateParts {
  const now = new Date();
  const dtf = new Intl.DateTimeFormat(locale, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  const tf = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return {
    dateLine: dtf.format(now),
    timeLine: tf.format(now),
  };
}

function useWallpaperClock(language: "zh" | "en") {
  const locale = language === "zh" ? "zh-CN" : "en-US";

  // Start with empty strings to avoid SSR/hydration mismatch.
  // The actual time is populated in useEffect after mount.
  const [parts, setParts] = useState<DateParts>({
    dateLine: "",
    timeLine: "",
  });

  useEffect(() => {
    // Populate immediately on mount.
    setParts(formatClock(locale));

    // Calculate ms until the next minute boundary to avoid drift.
    const now = Date.now();
    const msUntilNextMinute = 60_000 - (now % 60_000);

    const scheduleNext = () => {
      setParts(formatClock(locale));
    };

    // intervalId lives in the outer scope so the cleanup function can reach it.
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const nextMinuteTimer = setTimeout(() => {
      scheduleNext();
      intervalId = setInterval(scheduleNext, 60_000);
    }, msUntilNextMinute);

    return () => {
      clearTimeout(nextMinuteTimer);
      if (intervalId !== null) clearInterval(intervalId);
    };
  }, [locale]);

  return parts;
}

export function WallpaperClockOverlay() {
  const { language } = useI18n();
  const { dateLine, timeLine } = useWallpaperClock(language);

  if (!dateLine && !timeLine) {
    return null;
  }

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none select-none"
      style={{
        position: "absolute",
        top: "8%",
        left: "50%",
        transform: "translateX(-50%)",
        width: "82%",
        zIndex: 55,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "4px",
      }}
    >
      {/* Date line */}
      <span
        style={{
          fontSize: "clamp(22px, 4vw, 36px)",
          fontWeight: 500,
          color: "rgba(255,255,255,0.96)",
          letterSpacing: "0.015em",
          lineHeight: 1.2,
          textAlign: "center",
          textShadow: "0 2px 7px rgba(0,0,0,0.38), 0 0 2px rgba(0,0,0,0.55)",
          whiteSpace: "nowrap",
        }}
      >
        {dateLine}
      </span>

      {/* Time line */}
      <span
        style={{
          fontSize: "clamp(96px, 18vw, 168px)",
          fontWeight: 300,
          color: "rgba(255,255,255,0.98)",
          lineHeight: 0.92,
          letterSpacing: "-0.045em",
          textShadow: "0 3px 14px rgba(0,0,0,0.42), 0 0 4px rgba(0,0,0,0.58)",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {timeLine}
      </span>
    </div>
  );
}
