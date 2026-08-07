"use client";

import { useOnboardingStore } from "@/lib/hooks/useOnboardingStore";
import { ProfileSetupScreen } from "@/components/onboarding/ProfileSetupScreen";
import { PairingScreen } from "@/components/onboarding/PairingScreen";
import { PhotoUploadScreen } from "@/components/onboarding/PhotoUploadScreen";
import { ConnectionSuccessScreen } from "@/components/onboarding/ConnectionSuccessScreen";
import { PreludeStep } from "@/components/onboarding/PreludeStep";

export function OnboardingFlow() {
  const step = useOnboardingStore((s) => s.step);

  switch (step) {
    case "profile":
      return <ProfileSetupScreen />;
    case "pairing":
      return <PairingScreen />;
    case "photo":
      return <PhotoUploadScreen />;
    case "connected":
      return <ConnectionSuccessScreen />;
    case "prelude":
      return <PreludeStep />;
    case "wallpaper":
      return null;
    default:
      return null;
  }
}
