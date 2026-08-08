"use client";

import Image from "next/image";
import { resolveApiAssetUrl, type MemoryObjectItem } from "@/lib/api";
import { getSelectedWallpaper, useSceneStore } from "@/lib/hooks/useSceneStore";
import { useI18n } from "@/lib/i18n";

export function MemoryAssetsPanel({
  assets,
  isLoading,
  error,
  onClose,
  onRetry,
  onAssetSelect,
}: {
  assets: MemoryObjectItem[];
  isLoading: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: () => void;
  onAssetSelect?: (asset: MemoryObjectItem) => void;
}) {
  const { t } = useI18n();
  const selectedWallpaper = useSceneStore(getSelectedWallpaper);
  const backgroundImageUrl = selectedWallpaper?.imageUrl ?? "";

  return (
    <div className="asset-panel-enter absolute inset-0 z-[80] overflow-hidden bg-[#f3ede2] text-black">
      {backgroundImageUrl ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-cover bg-center bg-no-repeat"
          style={{
            backgroundImage: `url(${backgroundImageUrl})`,
            filter: "saturate(0.72) brightness(1.12)",
            opacity: 0.24,
            transform: "scale(1.035)",
          }}
        />
      ) : null}
      <div className="pointer-events-none absolute inset-0 bg-[rgba(250,246,238,0.34)]" />

      <button
        type="button"
        aria-label={t("memory.close")}
        onClick={onClose}
        className="absolute right-[clamp(16px,3.5vw,28px)] top-[clamp(16px,2.5vh,28px)] z-[100] flex h-[56px] w-[56px] items-center justify-center rounded-full bg-white/[0.82] text-black shadow-[0_7px_16px_rgba(58,48,39,0.15)] backdrop-blur-md transition hover:-translate-x-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-black active:translate-x-0"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-7 w-7"
          aria-hidden="true"
        >
          <path d="M19 12H5" />
          <path d="M12 19l-7-7 7-7" />
        </svg>
      </button>

      <section className="absolute left-1/2 top-[51%] flex h-[76%] w-[86%] max-w-[680px] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-[34px] border border-white/90 bg-white/[0.66] px-[clamp(22px,5vw,36px)] pb-[clamp(24px,3.2vh,38px)] pt-[clamp(24px,3.2vh,38px)] shadow-[0_18px_38px_rgba(74,60,47,0.18)] backdrop-blur-md">
        <div
          className="mx-auto h-[6px] w-[76px] rounded-full bg-black/30"
          aria-hidden
        />

        <header className="mt-[clamp(18px,2.6vh,30px)] text-center">
          <p className="text-[clamp(18px,3.1vw,23px)] font-extrabold uppercase tracking-[0.08em] text-[#736555]">
            {t("memory.collection")}
          </p>
          <h1 className="mt-2 text-[clamp(36px,6.4vw,49px)] font-extrabold leading-tight tracking-[-0.04em] text-black">
            {t("memory.book")}
          </h1>
          <p className="mt-2 text-[clamp(24px,4.3vw,32px)] font-bold leading-tight text-[#5c5044]">
            {t("memory.shared")}
          </p>
        </header>

        <div className="mt-[clamp(18px,2.6vh,30px)] flex items-center justify-between gap-4 border-b-2 border-black/15 pb-4">
          <div className="min-w-0">
            <p className="text-[clamp(22px,3.8vw,29px)] font-extrabold leading-tight text-black">
              {t("memory.recent")}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="whitespace-nowrap rounded-full bg-[#eee7da] px-4 py-2 text-[clamp(17px,2.8vw,21px)] font-bold text-[#55493d]">
              {t("memory.itemCount", { count: assets.length })}
            </span>
            <button
              type="button"
              onClick={onRetry}
              aria-label={t("memory.refresh")}
              className="flex h-[48px] w-[48px] items-center justify-center rounded-full bg-[#eee7da] text-[30px] font-bold leading-none text-[#55493d] transition hover:bg-[#e2d7c7] focus:outline-none focus-visible:ring-2 focus-visible:ring-black"
            >
              &#8635;
            </button>
          </div>
        </div>

        <div className="mt-[clamp(18px,2.8vh,30px)] min-h-0 flex-1 overflow-y-auto px-1 pr-2 [scrollbar-width:thin]">
          {isLoading && assets.length === 0 ? (
            <div className="flex h-full min-h-[260px] flex-col items-center justify-center gap-4 text-[clamp(23px,4vw,30px)] font-bold text-black/[0.55]">
              <span className="h-10 w-10 animate-spin rounded-full border-[4px] border-black/15 border-t-black/[0.55]" />
              <span>{t("memory.loading")}</span>
            </div>
          ) : error ? (
            <div className="flex h-full min-h-[260px] flex-col items-center justify-center px-5 text-center">
              <p className="text-[clamp(22px,3.8vw,29px)] font-bold leading-snug text-[#873b31]">
                {error}
              </p>
              <button
                type="button"
                onClick={onRetry}
                className="mt-6 rounded-full bg-black px-8 py-3 text-[clamp(20px,3.4vw,26px)] font-bold text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-black focus-visible:ring-offset-2"
              >
                {t("memory.retry")}
              </button>
            </div>
          ) : assets.length === 0 ? (
            <div className="flex h-full min-h-[260px] items-center justify-center px-6 text-center text-[clamp(24px,4.2vw,32px)] font-extrabold leading-snug text-[#5b5045]">
              {t("memory.empty")}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-x-[clamp(14px,3vw,24px)] gap-y-[clamp(20px,3vh,32px)] pb-3">
              {assets.map((asset) => (
                <button
                  key={asset.assetId || asset.name}
                  type="button"
                  onClick={() => onAssetSelect?.(asset)}
                  className="group flex min-w-0 flex-col items-center rounded-[24px] p-2 transition duration-200 hover:-translate-y-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-black"
                >
                  <span className="relative flex aspect-square w-full max-w-[210px] items-center justify-center overflow-hidden rounded-[24px] border-2 border-dashed border-[#d3c4ae] bg-white/[0.88] p-4 shadow-[0_10px_22px_rgba(76,61,47,0.15)]">
                    {asset.imageUrl ? (
                      <Image
                        src={resolveApiAssetUrl(asset.imageUrl)}
                        alt={asset.name}
                        fill
                        unoptimized
                        className="object-contain p-3"
                        sizes="210px"
                      />
                    ) : (
                      <span className="text-center text-[clamp(20px,3.4vw,26px)] font-extrabold text-[#706457]">
                        {asset.mentionCount}/{asset.threshold}
                      </span>
                    )}
                  </span>
                  <span className="mt-3 w-full truncate text-[clamp(23px,4vw,31px)] font-extrabold leading-tight text-black">
                    {asset.name}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <p className="mt-5 shrink-0 text-center text-[clamp(18px,3.1vw,23px)] font-bold text-black/[0.45]">
          {t("memory.swipeDown")}
        </p>
      </section>

      <button
        type="button"
        onClick={onClose}
        aria-label={t("memory.close")}
        className="absolute inset-x-0 bottom-0 z-[90] h-[12%] cursor-s-resize bg-transparent focus:outline-none"
      />

      <style jsx>{`
        .asset-panel-enter {
          animation: asset-panel-rise 420ms cubic-bezier(0.22, 0.78, 0.22, 1)
            both;
        }

        @keyframes asset-panel-rise {
          from {
            opacity: 0;
            transform: translateY(18%);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .asset-panel-enter {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
