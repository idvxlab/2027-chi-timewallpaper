"use client";

import { useEffect, useState } from "react";
import { getCurrentSession } from "@/lib/api";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { subscribeToRelationshipEvents } from "@/lib/relationshipEvents";
import { useI18n } from "@/lib/i18n";

function ProgressIndicator() {
  const { t } = useI18n();
  return (
    <div
      aria-label={t("pairing.stepLabel")}
      className="absolute bottom-[clamp(34px,5.5vh,66px)] left-1/2 z-20 flex -translate-x-1/2 items-center gap-[44px]"
    >
      <span className="h-[6px] w-[88px] rounded-full bg-black" />
      <span className="h-[6px] w-[88px] rounded-full bg-[#d8d7d4]" />
      <span className="h-[6px] w-[88px] rounded-full bg-[#d8d7d4]" />
    </div>
  );
}

function CodeDisplay({ code }: { code: string }) {
  const digits = code.padEnd(4, "-").slice(0, 4).split("");

  return (
    <div
      className="flex items-center justify-center gap-[clamp(14px,2.8vw,22px)]"
      aria-label={`Invitation code ${digits.join(" ")}`}
    >
      {digits.map((digit, index) => (
        <div
          key={`${digit}-${index}`}
          aria-hidden="true"
          className="flex h-[clamp(96px,13vw,120px)] w-[clamp(74px,10vw,92px)] items-center justify-center rounded-[10px] bg-white text-[clamp(54px,8vw,72px)] font-extrabold leading-none text-black shadow-[0_7px_16px_rgba(62,54,44,0.04)]"
        >
          {digit}
        </div>
      ))}
    </div>
  );
}

export function PairingScreen() {
  const { t } = useI18n();
  const userContext = useOnboardingStore((state) => state.userContext);
  const setUserContext = useOnboardingStore((state) => state.setUserContext);
  const setStep = useOnboardingStore((state) => state.setStep);
  const resetOnboarding = useOnboardingStore(
    (state) => state.resetOnboarding,
  );
  const [statusError, setStatusError] = useState<string | null>(null);
  const inviteCode = userContext?.inviteCode ?? "----";

  useEffect(() => {
    if (!userContext?.userId || !userContext.relationshipId) {
      setStep("profile");
      return;
    }
    let active = true;
    let unsubscribe = () => {};

    const refreshOnce = async () => {
      try {
        const session = await getCurrentSession();
        if (!active) return;
        if (!session) {
          // The invite code in memory belongs to a missing/expired backend
          // session. Clear the stale pairing screen instead of opening an
          // unauthenticated WebSocket and reconnecting forever.
          resetOnboarding();
          return;
        }

        setStatusError(null);
        setUserContext({
          userId: session.userId,
          counterpartUserId: session.counterpartUserId,
          relationshipId: session.relationshipId,
          relationshipDisplayName: session.relationshipDisplayName,
          inviteCode: session.inviteCode,
          relationshipStatus: session.relationshipStatus,
          viewerRole: session.viewerRole,
          counterpartRole: session.counterpartRole,
          familyRole: session.familyRole,
        });

        if (session.relationshipStatus === "connected") {
          setStep("photo");
          return;
        }

        unsubscribe = subscribeToRelationshipEvents(
          () => {
            if (!active) return;
            setStatusError(null);
            setStep("photo");
          },
          () => {
            if (active) {
              setStatusError(t("pairing.errorLost"));
            }
          },
        );
      } catch (error) {
        console.error("[PairingScreen] initial status lookup failed", error);
        if (active) {
          setStatusError(t("pairing.errorCheck"));
        }
      }
    };

    void refreshOnce();

    return () => {
      active = false;
      unsubscribe();
    };
  }, [
    resetOnboarding,
    setStep,
    setUserContext,
    t,
    userContext?.relationshipId,
    userContext?.userId,
  ]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#eee7da] text-black">
      <main className="relative z-10 flex h-full w-full flex-col items-center px-[clamp(28px,7vw,58px)] pt-[clamp(116px,14vh,188px)] text-center">
        <h1 className="text-[clamp(104px,20vw,154px)] font-normal leading-[0.9] tracking-[-0.065em] text-black">
          {t("pairing.hello")}
        </h1>

        <section className="mt-[clamp(112px,12vh,158px)] flex w-full max-w-[560px] flex-col items-center">
          <h2 className="text-[clamp(31px,5.3vw,40px)] font-extrabold leading-[1.12] tracking-[-0.025em] text-black">
            {t("pairing.title")}
          </h2>

          <p className="mt-[28px] max-w-[470px] text-[clamp(23px,4vw,30px)] font-semibold leading-[1.25] text-black">
            {t("pairing.shareLine1")}
            <br />
            {t("pairing.shareLine2")}
          </p>

          <div className="mt-[clamp(54px,6vh,76px)]">
            <CodeDisplay code={inviteCode} />
          </div>

          <div className="mt-[clamp(92px,10vh,126px)] flex w-full items-center justify-center gap-[10px] whitespace-nowrap text-left text-[clamp(15px,2.7vw,20px)] font-bold leading-none text-black">
            <span className="h-[18px] w-[18px] shrink-0 animate-pulse rounded-full bg-[#ffbc50]" />
            <span className="shrink-0">
              {t("pairing.waiting")}
            </span>
          </div>

          {statusError ? (
            <p className="mt-6 text-[clamp(17px,2.8vw,21px)] font-semibold text-[#8d382d]">
              {statusError}
            </p>
          ) : null}
        </section>
      </main>

      <ProgressIndicator />
    </div>
  );
}
