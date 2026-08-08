"use client";

import { useEffect, useState } from "react";
import { IpadFrame } from "@/components/shared/IpadFrame";
import { WallpaperStage } from "@/components/wallpaper/WallpaperStage";
import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";
import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { useSceneStore } from "@/lib/hooks/useSceneStore";
import {
  getCurrentSession,
  normalizeWallpaperImageUrl,
} from "@/lib/api";

export default function Page() {
  const step = useOnboardingStore((s) => s.step);
  const [isRestoring, setIsRestoring] = useState(true);

  useEffect(() => {
    let active = true;
    getCurrentSession()
      .then((session) => {
        if (!active) return;
        const onboarding = useOnboardingStore.getState();
        if (!session) {
          // A dev-server restart can preserve stale Zustand state while the
          // backend session cookie is gone. Return to onboarding instead of
          // mounting authenticated WebSockets with no valid session.
          onboarding.resetOnboarding();
          return;
        }
        onboarding.setRole(session.viewerRole);
        onboarding.setName(session.displayName);
        onboarding.setGender(session.gender);
        onboarding.setUserContext({
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
        // Migrate old demo/base-scene URLs saved in existing sessions.
        const restoredWallpaperUrl = normalizeWallpaperImageUrl(
          session.wallpaperUrl,
        );

        if (restoredWallpaperUrl) {
          useSceneStore.getState().setGeneratedWallpaperUrl(restoredWallpaperUrl);
        }
        // Do NOT set initialInsertStatus here — useWallpaperSync will query
        // the backend stage and set it to idle or ready accordingly.
        onboarding.setStep(session.onboardingStep);
      })
      .catch((error) => {
        console.error("[SessionRestore] failed", error);
      })
      .finally(() => {
        if (active) setIsRestoring(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // WallpaperStage already composes <AtmosphereLayer /> and
  // <ChatOverlay /> internally, so we deliberately do NOT mount
  // ChatOverlay here — doing so would render the overlay twice and
  // double every interaction handler.
  return (
    <main className="flex h-[100dvh] min-h-[100vh] w-full flex-col">
      {isRestoring ? (
        <div className="flex h-full w-full flex-1 items-center justify-center bg-white text-sm text-slate-500">
          Loading...
        </div>
      ) : step !== "wallpaper" ? (
        <div className="h-full w-full flex-1 bg-[#eee7da]">
          <OnboardingFlow />
        </div>
      ) : (
        <div className="h-full w-full flex-1">
          <WallpaperStage />
        </div>
      )}
    </main>
  );
}
