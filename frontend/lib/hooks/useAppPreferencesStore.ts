"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type AppLanguage = "en" | "zh";

export type AppPreferencesState = {
  language: AppLanguage;
  setLanguage: (language: AppLanguage) => void;
};

export const useAppPreferencesStore = create<AppPreferencesState>()(
  persist(
    (set) => ({
      language: "en",

      setLanguage(language) {
        set({ language });
      },
    }),
    {
      name: "timewallpaper_app_preferences",
      skipHydration: true,
    },
  ),
);
