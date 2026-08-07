"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import {
  getCurrentRelationshipCharacterAssets,
  getCurrentSession,
  resolveApiAssetUrl,
  STATIC_BASE_SCENE_PATH,
  updateSessionProgress,
  type CharacterAsset,
  type RelationshipCharacterAssets,
  type SessionResult,
} from "@/lib/api";
import {
  useOnboardingStore,
  type OnboardingRole,
  type OnboardingUserContext,
} from "@/lib/hooks/useOnboardingStore";
import { useSceneStore } from "@/lib/hooks/useSceneStore";
import { ProfileSettingsScreen } from "@/components/onboarding/ProfileSettingsScreen";
import { useI18n } from "@/lib/i18n";

type ParticipantNames = {
  elder: string;
  child: string;
};

function toUserContext(session: SessionResult): OnboardingUserContext {
  return {
    userId: session.userId,
    counterpartUserId: session.counterpartUserId,
    relationshipId: session.relationshipId,
    relationshipDisplayName: session.relationshipDisplayName,
    inviteCode: session.inviteCode,
    relationshipStatus: session.relationshipStatus,
    viewerRole: session.viewerRole,
    counterpartRole: session.counterpartRole,
    familyRole: session.familyRole,
  };
}

function assetPortraitUrl(asset: CharacterAsset | null): string {
  if (!asset) return "";
  return resolveApiAssetUrl(
    asset.portraitImageUrl ||
      asset.masterImageUrl ||
      asset.halfBodyImageUrl ||
      asset.sourceImageUrl,
  );
}

function resolveParticipantNames(
  session: SessionResult | null,
  viewerRole: Exclude<OnboardingRole, null>,
  storedName: string,
): ParticipantNames {
  const ownName = session?.displayName.trim() || storedName.trim();
  const relationshipName = session?.relationshipDisplayName ?? "";
  const namesOnly = relationshipName.replace(/^\d{4}-\d{2}-\d{2}-/, "");

  let counterpartName = "";
  if (
    ownName &&
    viewerRole === "elder" &&
    namesOnly.startsWith(`${ownName}-`)
  ) {
    counterpartName = namesOnly.slice(ownName.length + 1);
  } else if (
    ownName &&
    viewerRole === "child" &&
    namesOnly.endsWith(`-${ownName}`)
  ) {
    counterpartName = namesOnly.slice(0, -(ownName.length + 1));
  } else {
    const separatorIndex = namesOnly.indexOf("-");
    if (separatorIndex >= 0) {
      const elderName = namesOnly.slice(0, separatorIndex);
      const childName = namesOnly.slice(separatorIndex + 1);
      return {
        elder: elderName || "Older Adult",
        child: childName || "Adult Child",
      };
    }
  }

  if (viewerRole === "elder") {
    return {
      elder: ownName || "Older Adult",
      child: counterpartName || "Adult Child",
    };
  }

  return {
    elder: counterpartName || "Older Adult",
    child: ownName || "Adult Child",
  };
}

function Sparkle({
  className,
  color,
  size,
}: {
  className: string;
  color: string;
  size: number;
}) {
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute ${className}`}
      style={{
        width: size,
        height: size,
        backgroundColor: color,
        clipPath:
          "polygon(50% 0%, 64% 36%, 100% 50%, 64% 64%, 50% 100%, 36% 64%, 0% 50%, 36% 36%)",
      }}
    />
  );
}

function ParticipantPortrait({
  imageUrl,
  name,
  pillColor,
  className,
}: {
  imageUrl: string;
  name: string;
  pillColor: string;
  className?: string;
}) {
  return (
    <div
      className={`relative w-[43%] max-w-[340px] shrink-0 ${className ?? ""}`}
    >
      <div className="relative aspect-square w-full overflow-hidden rounded-full border-[8px] border-[#eee7da] bg-[#aaa6a6] shadow-[0_14px_28px_rgba(55,48,39,0.13)]">
        {imageUrl ? (
          <Image
            src={imageUrl}
            alt={`${name}'s AI portrait`}
            fill
            unoptimized
            sizes="(max-width: 640px) 43vw, 340px"
            className="object-cover"
          />
        ) : (
          <span className="absolute inset-0 animate-pulse bg-black/8" />
        )}
      </div>

      <div
        className="absolute -bottom-[14px] left-1/2 z-20 w-[78%] -translate-x-1/2 truncate rounded-full px-[22px] py-[9px] text-[clamp(20px,3.4vw,26px)] font-medium leading-none text-white shadow-[0_7px_14px_rgba(41,36,31,0.10)]"
        style={{ backgroundColor: pillColor }}
        title={name}
      >
        {name}
      </div>
    </div>
  );
}

function ProgressIndicator() {
  const { t } = useI18n();
  return (
    <div
      aria-label={t("connected.stepLabel")}
      className="absolute bottom-[clamp(30px,4.8vh,58px)] left-1/2 z-30 flex -translate-x-1/2 items-center gap-[44px]"
    >
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
    </div>
  );
}

function CornerControls({
  onBack,
  onOpenSettings,
  disabled = false,
}: {
  onBack: () => void;
  onOpenSettings: () => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const buttonClass =
    "absolute top-[clamp(18px,3vh,30px)] z-50 flex h-[58px] w-[58px] items-center justify-center rounded-full text-black transition hover:bg-black/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-black disabled:cursor-not-allowed disabled:opacity-35";

  return (
    <>
      <button
        type="button"
        onClick={onBack}
        disabled={disabled}
        aria-label={t("connected.backLabel")}
        className={`${buttonClass} left-[clamp(18px,4vw,34px)]`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          className="h-9 w-9"
        >
          <path
            d="m15.5 4.5-7.5 7.5 7.5 7.5"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      <button
        type="button"
        onClick={onOpenSettings}
        disabled={disabled}
        aria-label={t("connected.openSettings")}
        className={`${buttonClass} right-[clamp(18px,4vw,34px)]`}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-8 w-8">
          <g fill="currentColor">
            <circle cx="12" cy="12" r="7" />
            {Array.from({ length: 8 }, (_, index) => (
              <rect
                key={index}
                x="10.1"
                y="1.25"
                width="3.8"
                height="5.2"
                rx="0.55"
                transform={`rotate(${index * 45} 12 12)`}
              />
            ))}
          </g>
          <circle cx="12" cy="12" r="3.2" fill="#eee7da" />
        </svg>
      </button>
    </>
  );
}

export function ConnectionSuccessScreen() {
  const { t } = useI18n();
  const role = useOnboardingStore((state) => state.role);
  const storedName = useOnboardingStore((state) => state.name);
  const selfPhotoUrl = useOnboardingStore((state) => state.selfPhotoUrl);
  const partnerPhotoUrl = useOnboardingStore((state) => state.partnerPhotoUrl);
  const setUserContext = useOnboardingStore((state) => state.setUserContext);
  const setStep = useOnboardingStore((state) => state.setStep);

  const setGeneratedWallpaperUrl = useSceneStore(
    (state) => state.setGeneratedWallpaperUrl,
  );
  const incrementGenerationSession = useSceneStore(
    (state) => state.incrementGenerationSession,
  );

  const [session, setSession] = useState<SessionResult | null>(null);
  const [assets, setAssets] = useState<RelationshipCharacterAssets | null>(
    null,
  );
  const [isContinuing, setIsContinuing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const initDoneRef = useRef(false);
  const requestInFlightRef = useRef(false);

  const loadConnection = useCallback(async () => {
    if (requestInFlightRef.current) return;
    requestInFlightRef.current = true;

    try {
      const [latestSession, latestAssets] = await Promise.all([
        getCurrentSession(),
        getCurrentRelationshipCharacterAssets(),
      ]);

      setSession(latestSession ?? null);
      setAssets(latestAssets ?? null);

      if (latestSession) {
        setUserContext(toUserContext(latestSession));
      }
    } catch (loadError) {
      console.error("[ConnectionSuccess] init failed", loadError);
    } finally {
      requestInFlightRef.current = false;
    }
  }, [setUserContext]);

  useEffect(() => {
    if (initDoneRef.current) return;
    initDoneRef.current = true;
    void loadConnection();
  }, [loadConnection]);

  const viewerRole: Exclude<OnboardingRole, null> =
    role ?? session?.viewerRole ?? "child";

  const names = resolveParticipantNames(session, viewerRole, storedName);

  // Priority: API assets > store fallback
  const apiChildUrl = assetPortraitUrl(assets?.child ?? null);
  const apiElderUrl = assetPortraitUrl(assets?.elder ?? null);

  // viewerRole === "child" → self=child, partner=elder
  // viewerRole === "elder" → self=elder, partner=child
  const childPortraitUrl =
    apiChildUrl ||
    (viewerRole === "child" ? selfPhotoUrl : partnerPhotoUrl) ||
    "";
  const elderPortraitUrl =
    apiElderUrl ||
    (viewerRole === "elder" ? selfPhotoUrl : partnerPhotoUrl) ||
    "";

  // Use the static base scene as the only initial wallpaper — never demo data
  const initialWallpaperUrl = resolveApiAssetUrl(STATIC_BASE_SCENE_PATH);
  const canContinue = Boolean(initialWallpaperUrl && !isContinuing);

  const continueToWallpaper = async () => {
    if (!canContinue || !initialWallpaperUrl) return;

    setIsContinuing(true);
    try {
      incrementGenerationSession();
      setGeneratedWallpaperUrl(initialWallpaperUrl);
      // The static base wallpaper is not the result of first-voice generation.
      // initialInsertStatus stays idle until the user records their first voice.
      useSceneStore.getState().setInitialInsertStatus("idle");
      await updateSessionProgress({
        onboardingStep: "wallpaper",
        wallpaperUrl: initialWallpaperUrl,
      });
      setStep("wallpaper");
    } catch (continueError) {
      console.error(
        "[ConnectionSuccess] failed to open wallpaper",
        continueError,
      );
    } finally {
      setIsContinuing(false);
    }
  };

  if (settingsOpen) {
    return <ProfileSettingsScreen onBack={() => setSettingsOpen(false)} />;
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#eee7da] text-black">
      <CornerControls
        onBack={() => setStep("photo")}
        onOpenSettings={() => setSettingsOpen(true)}
        disabled={isContinuing}
      />

      <Sparkle className="left-[10%] top-[7%]" color="#ffffff" size={27} />
      <Sparkle className="right-[17%] top-[11%]" color="#999896" size={34} />
      <Sparkle className="left-[7%] top-[31%]" color="#939290" size={30} />
      <Sparkle className="left-[42%] top-[31%]" color="#ffc71a" size={32} />
      <Sparkle className="right-[8%] top-[36%]" color="#939290" size={28} />
      <Sparkle className="left-[15%] top-[65%]" color="#ffc71a" size={30} />
      <Sparkle className="right-[44%] top-[69%]" color="#939290" size={29} />
      <Sparkle className="right-[13%] top-[82%]" color="#ffc71a" size={31} />
      <Sparkle className="right-[6%] top-[62%]" color="#ffffff" size={25} />

      <main className="relative z-10 flex h-full w-full flex-col items-center px-[clamp(28px,7vw,58px)] pt-[clamp(88px,10vh,126px)] text-center">
        <h1 className="text-[clamp(42px,7vw,56px)] font-extrabold leading-[1.02] tracking-[-0.04em]">
          {t("connected.title")
            .split("\n")
            .map((line, i) => (
              <span key={i}>
                {line}
                <br />
              </span>
            ))}
        </h1>

        <section className="mt-[clamp(92px,11vh,136px)] flex w-full max-w-[760px] items-center justify-center -space-x-[clamp(30px,5vw,44px)]">
          <ParticipantPortrait
            imageUrl={childPortraitUrl}
            name={names.child}
            pillColor="#080808"
            className="z-10"
          />
          <ParticipantPortrait
            imageUrl={elderPortraitUrl}
            name={names.elder}
            pillColor="#7e7e7e"
            className="z-20"
          />
        </section>
      </main>

      <button
        type="button"
        onClick={() => void continueToWallpaper()}
        disabled={!canContinue}
        aria-label={t("connected.continue")}
        className={[
          "absolute bottom-[clamp(116px,14vh,164px)] left-1/2 z-30 flex h-[86px] w-[86px] -translate-x-1/2 items-center justify-center rounded-full",
          "text-white shadow-[0_13px_25px_rgba(42,37,32,0.15)] transition duration-200",
          "focus:outline-none focus-visible:ring-3 focus-visible:ring-black focus-visible:ring-offset-3 focus-visible:ring-offset-[#eee7da]",
          canContinue
            ? "cursor-pointer bg-black hover:-translate-x-1/2 hover:-translate-y-1 active:translate-y-0"
            : "cursor-not-allowed bg-black/35",
        ].join(" ")}
      >
        {isContinuing ? (
          <span className="h-7 w-7 animate-spin rounded-full border-[3px] border-white/35 border-t-white" />
        ) : (
          <svg
            aria-hidden="true"
            viewBox="0 0 52 24"
            fill="none"
            className="h-8 w-[48px]"
          >
            <path
              d="M2 12h44M35 2l11 10-11 10"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>

      <ProgressIndicator />
    </div>
  );
}
