"use client";

import { useState } from "react";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { useI18n } from "@/lib/i18n";

export function ProfileSettingsScreen({ onBack }: { onBack: () => void }) {
  const { language, setLanguage, t } = useI18n();
  const role = useOnboardingStore((state) => state.role);
  const setRole = useOnboardingStore((state) => state.setRole);

  const [previewFontSize, setPreviewFontSize] = useState(78);
  const [previewFontWeight, setPreviewFontWeight] = useState(500);

  const previewText = language === "zh" ? t("settings.previewNihao") : t("settings.previewHello");

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#eee7da] text-black">
      <header className="absolute inset-x-0 top-0 z-20 flex h-[clamp(92px,11vh,126px)] items-center justify-center px-[clamp(24px,6vw,52px)]">
        <button
          type="button"
          onClick={onBack}
          aria-label={t("settings.backLabel")}
          className="absolute left-[clamp(24px,6vw,52px)] flex h-[58px] w-[58px] items-center justify-center rounded-full transition hover:bg-black/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-black"
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            className="h-9 w-9"
          >
            <path
              d="m15 4-8 8 8 8"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        <h1 className="text-[clamp(34px,5.6vw,44px)] font-semibold tracking-[-0.035em]">
          {t("settings.title")}
        </h1>
      </header>

      <main className="relative flex h-full w-full flex-col px-[clamp(32px,8vw,68px)] pt-[clamp(126px,15vh,172px)]">
        <section className="w-full">
          <label
            htmlFor="settings-mode"
            className="block text-[clamp(27px,4.6vw,35px)] font-medium"
          >
            {t("settings.mode")}
          </label>
          <select
            id="settings-mode"
            value={role ?? "child"}
            onChange={(event) =>
              setRole(event.target.value === "elder" ? "elder" : "child")
            }
            className="mt-3 h-[72px] w-full rounded-[16px] border border-black/10 bg-white px-5 text-[clamp(23px,4vw,30px)] font-medium text-black outline-none shadow-[0_7px_16px_rgba(50,44,37,0.04)] focus:border-black/35 focus:ring-2 focus:ring-black/10"
          >
            <option value="elder">{t("settings.olderAdults")}</option>
            <option value="child">{t("settings.adultChildren")}</option>
          </select>
        </section>

        <section className="mt-[34px] w-full">
          <label
            htmlFor="settings-language"
            className="block text-[clamp(27px,4.6vw,35px)] font-medium"
          >
            {t("settings.language")}
          </label>
          <select
            id="settings-language"
            value={language}
            onChange={(event) =>
              setLanguage(event.target.value === "zh" ? "zh" : "en")
            }
            className="mt-3 h-[72px] w-full rounded-[16px] border border-black/10 bg-white px-5 text-[clamp(23px,4vw,30px)] font-medium text-black outline-none shadow-[0_7px_16px_rgba(50,44,37,0.04)] focus:border-black/35 focus:ring-2 focus:ring-black/10"
          >
            <option value="en">English</option>
            <option value="zh">中文</option>
          </select>
        </section>

        <section className="mt-[38px] w-full">
          <h2 className="text-[clamp(27px,4.6vw,35px)] font-medium">
            {t("settings.font")}
          </h2>
          <div className="mt-3 flex h-[clamp(196px,23vh,250px)] w-full items-center justify-center rounded-[16px] border-2 border-dashed border-black/18 bg-white px-5">
            <span
              className="max-w-full truncate leading-none tracking-[-0.055em]"
              style={{
                fontSize: `${previewFontSize}px`,
                fontWeight: previewFontWeight,
              }}
            >
              {previewText}
            </span>
          </div>
        </section>

        <section className="mt-[30px] w-full">
          <div className="flex items-end justify-between gap-4">
            <label
              htmlFor="preview-font-size"
              className="text-[clamp(23px,4vw,30px)] font-medium"
            >
              {t("settings.fontSize")}
            </label>
            <span className="text-[clamp(17px,2.8vw,21px)] font-medium italic text-black/38">
              {t("settings.fontSizeLabel")}
            </span>
          </div>
          <input
            id="preview-font-size"
            type="range"
            min="48"
            max="112"
            step="1"
            value={previewFontSize}
            onChange={(event) => setPreviewFontSize(Number(event.target.value))}
            className="mt-3 h-[22px] w-full cursor-pointer accent-black"
            aria-valuetext={`${previewFontSize} pixels`}
          />
        </section>

        <section className="mt-[24px] w-full">
          <div className="flex items-end justify-between gap-4">
            <label
              htmlFor="preview-font-weight"
              className="text-[clamp(23px,4vw,30px)] font-medium"
            >
              {t("settings.fontBold")}
            </label>
            <span className="text-[clamp(17px,2.8vw,21px)] font-medium italic text-black/38">
              {t("settings.fontBoldLabel")}
            </span>
          </div>
          <input
            id="preview-font-weight"
            type="range"
            min="300"
            max="900"
            step="100"
            value={previewFontWeight}
            onChange={(event) =>
              setPreviewFontWeight(Number(event.target.value))
            }
            className="mt-3 h-[22px] w-full cursor-pointer accent-black"
            aria-valuetext={`${previewFontWeight} font weight`}
          />
        </section>
      </main>
    </div>
  );
}
