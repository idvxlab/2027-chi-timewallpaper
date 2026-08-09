"use client";

import { useEffect } from "react";

import {
  getCurrentWallpaper,
  getWallpaperInteractions,
  getWallpaperRevisions,
  normalizeWallpaperImageUrl,
} from "@/lib/api";
import { subscribeToWallpaperEvents } from "@/lib/wallpaperEvents";
import { useSceneStore } from "./useSceneStore";
import { setWallpaperVoiceEditStatus } from "./useWallpaperVoiceEditRecorder";

/** Keep this device on the latest role-specific relationship wallpaper. */
export function useWallpaperSync() {
  useEffect(() => {
    let active = true;
    let requestInFlight = false;

    const refresh = async () => {
      if (!active || requestInFlight || document.visibilityState === "hidden") {
        return;
      }
      requestInFlight = true;
      try {
        const [current, history, interactions] = await Promise.all([
          getCurrentWallpaper(),
          getWallpaperRevisions(),
          getWallpaperInteractions().catch((error) => {
            console.debug("[WallpaperSync] interaction history unavailable", error);
            return { relationshipId: "", items: [] };
          }),
        ]);

        if (!active) return;

        // Rewrite legacy base-scene paths before they enter the store.
        const normalizedHistory = history.items.map((item) => ({
          ...item,
          imageUrl: normalizeWallpaperImageUrl(item.imageUrl),
        }));
        const currentWallpaperUrl = normalizeWallpaperImageUrl(
          current.wallpaperUrl,
        );

        const scene = useSceneStore.getState();

        // ── Write all authoritative backend data BEFORE lifting the
        //     hydration gate. Hydration must reflect a fully-written
        //     store so the interaction mode leaves "loading" only after
        //     current/history/stage are all in place.

        // 1. Wallpaper revisions
        scene.setWallpaperRevisions(normalizedHistory);
        scene.setWallpaperInteractions(interactions.items);

        // The revision write above may replace dated buckets and selection.
        // Re-read state before comparing the independently authoritative
        // current URL/version; the earlier snapshot is intentionally stale.
        const refreshedScene = useSceneStore.getState();

        // 2. Current wallpaper URL / version (active stage only)
        const hasActiveWallpaper =
          current.stage === "wallpaper_active" &&
          !!currentWallpaperUrl;
        const hasDisplayWallpaper = !!currentWallpaperUrl;
        const versionAdvanced =
          current.version > refreshedScene.wallpaperVersion;

        if (hasDisplayWallpaper) {
          const hasStaleUrl =
            currentWallpaperUrl !== refreshedScene.generatedWallpaperUrl;
          if (versionAdvanced || hasStaleUrl) {
            refreshedScene.setGeneratedWallpaperUrl(currentWallpaperUrl);
          }
        }
        if (hasActiveWallpaper) {
          refreshedScene.setWallpaperVersion(current.version);
          if (versionAdvanced && current.status === "idle") {
            setWallpaperVoiceEditStatus("idle");
          }
        }

        // 3. Backend stage → initialInsertStatus
        if (hasActiveWallpaper) {
          refreshedScene.setInitialInsertStatus("ready");
        } else if (
          current.stage === "characters_ready" ||
          current.stage === "awaiting_characters"
        ) {
          refreshedScene.setInitialInsertStatus("idle");
        }

        // 4. Finally lift the hydration gate — only now is the store
        //    fully consistent for a single refresh.
        refreshedScene.setWallpaperStateHydrated(true);
      } catch (error) {
        console.debug("[WallpaperSync] refresh skipped", error);
        // Do NOT set hydrated on failure — keep interactionMode in
        // "loading" so the previous safe state is preserved. The next
        // WebSocket event or visibilitychange will retry.
      } finally {
        requestInFlight = false;
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    void refresh();
    const unsubscribe = subscribeToWallpaperEvents((event) => {
      if (
        event.status === "chatbot_reply_ready" &&
        event.version !== undefined &&
        event.transcript &&
        (event.speakerRole === "child" || event.speakerRole === "elder")
      ) {
        useSceneStore.getState().setWallpaperInteractions([
          {
            eventId: event.eventId || `event-${event.version}`,
            eventSeq: event.version,
            status: event.status,
            transcript: event.transcript,
            reply: event.reply || "",
            suggestedReplies: event.suggestedReplies,
            speakerRole: event.speakerRole,
            createdAt: event.createdAt || new Date().toISOString(),
          },
        ]);
      }
      if (
        ["wallpaper_revision_queued", "wallpaper_revision_retrying"].includes(
          event.status,
        )
      ) {
        setWallpaperVoiceEditStatus("editing");
      } else if (
        ["wallpaper_revision_ready", "wallpaper_revision_failed"].includes(
          event.status,
        )
      ) {
        setWallpaperVoiceEditStatus("idle");
      }
      void refresh();
    });
    const fallbackPoll = window.setInterval(() => void refresh(), 5_000);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      active = false;
      window.clearInterval(fallbackPoll);
      unsubscribe();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);
}
