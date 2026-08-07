"use client";

import { create } from "zustand";

export type OnboardingStep =
  | "profile"
  | "pairing"
  | "photo"
  | "connected"
  | "prelude"
  | "wallpaper";
export type OnboardingRole = "elder" | "child" | null;

export type OnboardingUserContext = {
  userId: string;
  counterpartUserId: string;
  relationshipId: string;
  relationshipDisplayName: string;
  inviteCode: string | null;
  relationshipStatus: "waiting" | "connected";
  viewerRole: Exclude<OnboardingRole, null>;
  counterpartRole: Exclude<OnboardingRole, null>;
  familyRole: "mother" | "father" | "daughter" | "son";
};

export type OnboardingState = {
  step: OnboardingStep;
  role: OnboardingRole;
  name: string;
  gender: string;
  userContext: OnboardingUserContext | null;
  // Preview URLs — used by UI components only.
  selfPhotoUrl: string | null;
  partnerPhotoUrl: string | null;
  // Raw File objects — used by Step 3 API calls.
  // Stored separately so they survive blob-URL revocation after
  // PhotoUploadScreen unmounts.
  selfPhotoFile: File | null;
  partnerPhotoFile: File | null;
  hasInitialWallpaper: boolean;

  setRole: (role: Exclude<OnboardingRole, null>) => void;
  setName: (name: string) => void;
  setGender: (gender: string) => void;
  setUserContext: (context: OnboardingUserContext | null) => void;
  setSelfPhotoUrl: (url: string | null) => void;
  setPartnerPhotoUrl: (url: string | null) => void;
  setSelfPhotoFile: (file: File | null) => void;
  setPartnerPhotoFile: (file: File | null) => void;

  setStep: (step: OnboardingStep) => void;
  goNext: () => void;
  resetOnboarding: () => void;
};

const STEP_ORDER: OnboardingStep[] = [
  "profile",
  "pairing",
  "photo",
  "connected",
  "prelude",
  "wallpaper",
];

const INITIAL_STATE: Pick<
  OnboardingState,
  | "step"
  | "role"
  | "name"
  | "gender"
  | "userContext"
  | "selfPhotoUrl"
  | "partnerPhotoUrl"
  | "selfPhotoFile"
  | "partnerPhotoFile"
  | "hasInitialWallpaper"
> = {
  step: "profile",
  role: null,
  name: "",
  gender: "",
  userContext: null,
  selfPhotoUrl: null,
  partnerPhotoUrl: null,
  selfPhotoFile: null,
  partnerPhotoFile: null,
  hasInitialWallpaper: false,
};

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  ...INITIAL_STATE,

  setRole(role) {
    set({ role });
  },
  setName(name) {
    set({ name });
  },
  setGender(gender) {
    set({ gender });
  },
  setUserContext(userContext) {
    set({ userContext });
  },
  setSelfPhotoUrl(url) {
    set({ selfPhotoUrl: url });
  },
  setPartnerPhotoUrl(url) {
    set({ partnerPhotoUrl: url });
  },
  setSelfPhotoFile(file) {
    set({ selfPhotoFile: file });
  },
  setPartnerPhotoFile(file) {
    set({ partnerPhotoFile: file });
  },

  setStep(step) {
    set({ step });
  },

  goNext() {
    const current = get().step;
    const idx = STEP_ORDER.indexOf(current);
    if (idx < 0 || idx >= STEP_ORDER.length - 1) return;
    const next = STEP_ORDER[idx + 1];
    set({ step: next });
  },

  resetOnboarding() {
    set({ ...INITIAL_STATE });
  },
}));
