"use client";

import { useState, type KeyboardEvent } from "react";
import {
  createOnboardingProfile,
  joinRelationship,
  type OnboardingProfileResult,
} from "@/lib/api";
import {
  useOnboardingStore,
  type OnboardingRole,
} from "@/lib/hooks/useOnboardingStore";
import { ProfileSettingsScreen } from "./ProfileSettingsScreen";
import { useI18n } from "@/lib/i18n";

type Role = Exclude<OnboardingRole, null>;
type Gender = "female" | "male";

const ROLE_OPTIONS: Array<{
  value: Role;
  labelKey: "profile.olderAdults" | "profile.adultChildren";
}> = [
  { value: "elder", labelKey: "profile.olderAdults" },
  { value: "child", labelKey: "profile.adultChildren" },
];

const DEFAULT_PROFILE_BY_ROLE: Record<
  Role,
  { displayName: string; gender: Gender }
> = {
  elder: { displayName: "Older Adult", gender: "female" },
  child: { displayName: "Adult Child", gender: "female" },
};

function ChoicePill({
  selected,
  label,
  onClick,
}: {
  selected: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={[
        "flex h-[66px] min-w-0 flex-1 items-center justify-center rounded-full",
        "border border-black/50 px-5 text-[clamp(24px,4vw,31px)] font-semibold leading-none",
        "transition duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-black focus-visible:ring-offset-2 focus-visible:ring-offset-[#eee7da]",
        selected
          ? "border-transparent bg-[#aaa6a6] text-white shadow-[0_9px_18px_rgba(58,52,45,0.10)]"
          : "bg-white/85 text-[#666568] hover:-translate-y-0.5 hover:bg-white",
      ].join(" ")}
    >
      <span className="whitespace-nowrap">{label}</span>
    </button>
  );
}

function ProgressIndicator() {
  const { t } = useI18n();
  return (
    <div
      aria-label={t("profile.stepLabel", { n: "1" })}
      className="absolute bottom-[clamp(36px,6vh,66px)] left-1/2 flex -translate-x-1/2 items-center gap-[44px]"
      role="progressbar"
      aria-valuemin={1}
      aria-valuemax={3}
      aria-valuenow={1}
    >
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
      <span className="h-[6px] w-[88px] rounded-full bg-[#d7d5d0]" />
      <span className="h-[6px] w-[88px] rounded-full bg-[#d7d5d0]" />
    </div>
  );
}

export function ProfileSetupScreen() {
  const { t } = useI18n();
  const role = useOnboardingStore((state) => state.role);
  const name = useOnboardingStore((state) => state.name);
  const gender = useOnboardingStore((state) => state.gender);
  const userContext = useOnboardingStore((state) => state.userContext);
  const setRole = useOnboardingStore((state) => state.setRole);
  const setName = useOnboardingStore((state) => state.setName);
  const setGender = useOnboardingStore((state) => state.setGender);
  const setUserContext = useOnboardingStore((state) => state.setUserContext);
  const setStep = useOnboardingStore((state) => state.setStep);

  const [inviteCode, setInviteCode] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canCreate = role !== null && !isSaving;
  const canJoin = canCreate && /^\d{4}$/.test(inviteCode);

  const getEffectiveProfile = (selectedRole: Role) => {
    const defaults = DEFAULT_PROFILE_BY_ROLE[selectedRole];
    const displayName = name.trim() || defaults.displayName;
    const effectiveGender: Gender =
      gender === "male" || gender === "female" ? gender : defaults.gender;

    if (!name.trim()) setName(displayName);
    if (gender !== "male" && gender !== "female") {
      setGender(effectiveGender);
    }

    return { displayName, gender: effectiveGender };
  };

  const applyProfile = (profile: OnboardingProfileResult) => {
    setUserContext({
      userId: profile.userId,
      counterpartUserId: profile.counterpartUserId,
      relationshipId: profile.relationshipId,
      relationshipDisplayName: profile.relationshipDisplayName,
      inviteCode: profile.inviteCode,
      relationshipStatus: profile.relationshipStatus,
      viewerRole: profile.viewerRole,
      counterpartRole: profile.counterpartRole,
      familyRole: profile.familyRole,
    });
    setStep(profile.relationshipStatus === "waiting" ? "pairing" : "photo");
  };

  const onCreateLink = async () => {
    if (!canCreate || role === null) return;

    const profileValues = getEffectiveProfile(role);
    setIsSaving(true);
    setError(null);

    try {
      const profile = await createOnboardingProfile({
        viewerRole: role,
        displayName: profileValues.displayName,
        gender: profileValues.gender,
        ...(userContext?.relationshipId
          ? {
              userId: userContext.userId,
              relationshipId: userContext.relationshipId,
            }
          : {}),
      });
      applyProfile(profile);
    } catch (requestError) {
      console.error("[ProfileSetup] profile setup failed", requestError);
      setError(t("profile.errorCreate"));
    } finally {
      setIsSaving(false);
    }
  };

  const onJoin = async () => {
    if (!canJoin || role === null) return;

    const profileValues = getEffectiveProfile(role);
    setIsSaving(true);
    setError(null);

    try {
      const profile = await joinRelationship({
        inviteCode,
        viewerRole: role,
        displayName: profileValues.displayName,
        gender: profileValues.gender,
      });
      applyProfile(profile);
    } catch (requestError) {
      console.error("[ProfileSetup] relationship join failed", requestError);
      setError(t("profile.errorJoin"));
    } finally {
      setIsSaving(false);
    }
  };

  const onInviteKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && canJoin) void onJoin();
  };

  if (settingsOpen) {
    return <ProfileSettingsScreen onBack={() => setSettingsOpen(false)} />;
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#eee7da] text-black">
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        disabled={isSaving}
        aria-label={t("common.settings")}
        className="absolute right-[clamp(24px,5vw,44px)] top-[clamp(24px,4vh,42px)] z-30 flex h-[58px] w-[58px] items-center justify-center rounded-full text-black transition hover:bg-black/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-black disabled:cursor-not-allowed disabled:opacity-35"
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

      <main className="relative flex h-full w-full flex-col items-center px-[clamp(28px,8vw,68px)] pt-[clamp(116px,14vh,188px)]">
        <h1 className="text-center text-[clamp(104px,20vw,154px)] font-normal leading-[0.9] tracking-[-0.055em] text-black">
          Beside
        </h1>

        <section className="mt-[clamp(96px,11vh,146px)] w-full max-w-[540px]">
          <h2 className="text-center text-[clamp(31px,5vw,40px)] font-bold leading-tight text-black">
            {t("profile.whoAreYou")}
          </h2>

          <div className="mt-[22px] flex w-full gap-[18px]">
            {ROLE_OPTIONS.map((option) => (
              <ChoicePill
                key={option.value}
                selected={role === option.value}
                label={t(option.labelKey)}
                onClick={() => setRole(option.value)}
              />
            ))}
          </div>
        </section>

        <div className="mt-[clamp(76px,8.5vh,114px)] flex w-full max-w-[540px] flex-col items-center">
          <button
            type="button"
            disabled={!canCreate}
            onClick={() => void onCreateLink()}
            className={[
              "flex h-[74px] w-full items-center justify-center gap-6 rounded-full",
              "bg-black px-8 text-[clamp(28px,4.5vw,35px)] font-medium text-white",
              "shadow-[0_12px_24px_rgba(55,48,39,0.12)] transition duration-200",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-black focus-visible:ring-offset-2 focus-visible:ring-offset-[#eee7da]",
              canCreate
                ? "cursor-pointer hover:-translate-y-0.5 hover:bg-[#181818] active:translate-y-0"
                : "cursor-not-allowed opacity-35",
            ].join(" ")}
          >
            <span>
              {isSaving ? t("profile.saving") : t("profile.createLink")}
            </span>
            {!isSaving ? (
              <svg
                aria-hidden="true"
                viewBox="0 0 52 32"
                className="h-9 w-[58px]"
                fill="none"
              >
                <path
                  d="M2 16h45M35 4l12 12-12 12"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ) : null}
          </button>

          <div className="mt-[26px] flex w-full items-stretch gap-[24px]">
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              aria-label="4-digit invitation code"
              value={inviteCode}
              onChange={(event) =>
                setInviteCode(event.target.value.replace(/\D/g, "").slice(0, 4))
              }
              onKeyDown={onInviteKeyDown}
              placeholder={t("profile.invitePlaceholder")}
              className="h-[68px] min-w-0 flex-1 rounded-full border border-black/45 bg-white/85 px-5 text-center text-[clamp(25px,4.2vw,32px)] font-medium text-[#666568] outline-none placeholder:text-[#777678] focus:border-black focus:ring-2 focus:ring-black/15"
            />

            <button
              type="button"
              disabled={!canJoin}
              onClick={() => void onJoin()}
              className={[
                "h-[68px] w-[132px] shrink-0 rounded-full border border-black/45",
                "text-[clamp(25px,4.2vw,32px)] font-medium transition duration-200",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-black focus-visible:ring-offset-2 focus-visible:ring-offset-[#eee7da]",
                canJoin
                  ? "cursor-pointer bg-black text-white hover:-translate-y-0.5"
                  : "cursor-not-allowed bg-[#e8e9eb] text-black/40",
              ].join(" ")}
            >
              {t("profile.join")}
            </button>
          </div>

          {error ? (
            <p className="mt-4 text-center text-[20px] font-semibold leading-7 text-[#8f332d]">
              {error}
            </p>
          ) : null}
        </div>
      </main>

      <ProgressIndicator />
    </div>
  );
}
