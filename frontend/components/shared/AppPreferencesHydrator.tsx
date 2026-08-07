"use client";

import { useEffect } from "react";
import { useAppPreferencesStore } from "@/lib/hooks/useAppPreferencesStore";

/**
 * AppPreferencesHydrator
 *
 * Client-only component that:
 * 1. Rehydrates the persisted language preference from localStorage on mount
 *    (avoids hydration mismatch when the server-rendered HTML defaults to "en"
 *    but localStorage already has "zh")
 * 2. Syncs `<html lang="...">` attribute whenever the language changes
 */
export function AppPreferencesHydrator() {
  const language = useAppPreferencesStore((s) => s.language);

  // Step 1: rehydrate on mount (runs once, after the component hydrates)
  useEffect(() => {
    // Tell zustand-persist to rehydrate from storage now that we're in the browser
    void useAppPreferencesStore.persist.rehydrate();
  }, []);

  // Step 2: keep document lang in sync with store
  useEffect(() => {
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
  }, [language]);

  // This component renders nothing — it's purely a side-effect wrapper
  return null;
}
