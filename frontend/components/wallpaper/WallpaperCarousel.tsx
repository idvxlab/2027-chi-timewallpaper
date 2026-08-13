"use client";

/**
 * WallpaperCarousel
 *
 * A true horizontal translate3d carousel that replaces the crossfade-based
 * history paging previously baked into AtmosphereLayer. Apple-style:
 * - viewport overflow-hidden
 * - inner track with display:flex and width = N × 100%
 * - every available wallpaper is mounted as its own flex:0 0 100% page
 * - pointerMove writes track.style.transform imperatively (no setState)
 * - snap uses a CSS transition on the track's transform
 * - store commit fires from transitionend, NOT from a fixed setTimeout
 *
 * The carousel only manages visual paging. The actual generated / latest
 * wallpaper for `today` still flows from the store (AtmosphereLayer inside).
 *
 * Carousel handoff contract with WallpaperStage:
 *   - It receives a `onPageSettled` callback that commits the new index.
 *   - It is *not* responsible for voice-gesture ownership; the parent
 *     decides which gesture is active and tells the carousel via
 *     `gestureMode` (we expose imperative methods through a ref handle).
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type CarouselPage = {
  /** Globally unique key (revisionId preferred). */
  key: string;
  /** Wallpaper image URL for this page. */
  imageUrl: string;
};

export type WallpaperCarouselHandle = {
  /** Programmatic jump with snap animation. Returns when snap has finished. */
  snapTo(index: number): Promise<void>;
  /** Returns the page index currently aligned with the viewport (post-snap). */
  getCurrentIndex(): number;
};

type Props = {
  pages: CarouselPage[];
  initialIndex: number;
  /** CSS class for each page's image (e.g. focus transforms). */
  pageClassName?: string;
  /** Whether carousel paging is allowed right now (gesture & mode gate). */
  enabled: boolean;
  /** Notifies parent when a snap animation finishes — store should commit. */
  onPageSettled: (index: number) => void;
  /** Reports the in-flight drag offset to parent (visual real-time). */
  onDrag?: (deltaX: number) => void;
  /** Reports whether a drag is currently active. */
  onDragChange?: (active: boolean) => void;
};

const SNAP_DURATION_MS = 260;
const SWIPE_COMMIT_RATIO = 0.18;
const SWIPE_COMMIT_MIN_PX = 60;
const FALLBACK_TIMEOUT_MS = 600;

export const WallpaperCarousel = forwardRef<WallpaperCarouselHandle, Props>(
  function WallpaperCarousel(
    {
      pages,
      initialIndex,
      pageClassName,
      enabled,
      onPageSettled,
      onDrag,
      onDragChange,
    },
    ref,
  ) {
    const trackRef = useRef<HTMLDivElement | null>(null);
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const [width, setWidth] = useState(0);
    const [currentIndex, setCurrentIndex] = useState(initialIndex);
    const currentIndexRef = useRef(initialIndex);

    useEffect(() => {
      currentIndexRef.current = currentIndex;
    }, [currentIndex]);

    // Snapshot of pointer state at gesture start.
    const pointerIdRef = useRef<number | null>(null);
    const startClientXRef = useRef<number | null>(null);
    const startClientYRef = useRef<number | null>(null);
    const baseIndexRef = useRef<number>(initialIndex);
    const draggingRef = useRef(false);
    const suppressClickUntilRef = useRef<number>(0);

    // Keep baseIndex in sync with controlled initialIndex when not dragging.
    useEffect(() => {
      if (!draggingRef.current) {
        baseIndexRef.current = currentIndexRef.current;
        setCurrentIndex(initialIndex);
      }
    }, [initialIndex]);

    // Observe viewport width for the drag delta calculation.
    useLayoutEffect(() => {
      if (!viewportRef.current) return;
      const node = viewportRef.current;
      const update = () => {
        const w = node.getBoundingClientRect().width;
        if (w > 0) setWidth(w);
      };
      update();
      const ro = new ResizeObserver(update);
      ro.observe(node);
      return () => ro.disconnect();
    }, []);

    const applyTransform = useCallback(
      (index: number, deltaX: number, animate: boolean) => {
        const track = trackRef.current;
        if (!track || width === 0) return;
        const offsetPx = -(index * width) + deltaX;
        track.style.transition = animate
          ? `transform ${SNAP_DURATION_MS}ms cubic-bezier(0.22, 0.61, 0.36, 1)`
          : "none";
        track.style.transform = `translate3d(${offsetPx}px, 0, 0)`;
      },
      [width],
    );

    const completeTransition = useCallback(
      (finalIndex: number) => {
        const track = trackRef.current;
        if (!track) return;
        track.style.transition = "none";
        track.style.transform = `translate3d(${-(finalIndex * width)}px, 0, 0)`;
        // Force a layout flush so the next CSS transition starts from the
        // "snap" position rather than the in-flight animated position.
        void track.offsetHeight;
      },
      [width],
    );

    const animateTo = useCallback(
      (index: number) => {
        const track = trackRef.current;
        if (!track || width === 0) return Promise.resolve();
        return new Promise<void>((resolve) => {
          const node = track;
          const handler = (e: TransitionEvent) => {
            if (e.target !== node || e.propertyName !== "transform") return;
            node.removeEventListener("transitionend", handler);
            node.removeEventListener("transitioncancel", handler);
            resolve();
          };
          node.addEventListener("transitionend", handler, { once: true });
          node.addEventListener("transitioncancel", handler, { once: true });
          // Fallback in case transitionend never fires (e.g. display:none).
          window.setTimeout(() => {
            node.removeEventListener("transitionend", handler);
            node.removeEventListener("transitioncancel", handler);
            resolve();
          }, FALLBACK_TIMEOUT_MS);
          // Defer to the next frame so transition: none (just applied) doesn't
          // cancel the upcoming animated transform.
          requestAnimationFrame(() => {
            track.style.transition = `transform ${SNAP_DURATION_MS}ms cubic-bezier(0.22, 0.61, 0.36, 1)`;
            track.style.transform = `translate3d(${-(index * width)}px, 0, 0)`;
          });
        });
      },
      [width],
    );

    // Imperative handle for parent.
    useImperativeHandle(
      ref,
      () => ({
        snapTo: (index: number) => animateTo(index),
        getCurrentIndex: () => currentIndexRef.current,
      }),
      [animateTo],
    );

    // Mount / index change → snap without animation on first paint only.
    useLayoutEffect(() => {
      if (!trackRef.current || width === 0) return;
      completeTransition(initialIndex);
      setCurrentIndex(initialIndex);
    }, [initialIndex, width, completeTransition]);

    // ── Pointer event handling (capture phase — matches parent) ─────────
    const isInteractiveTarget = useCallback((target: EventTarget | null) => {
      if (!(target instanceof Element)) return false;
      return !!target.closest(
        "button, input, textarea, select, [role='button'], [data-panorama-hold-control]",
      );
    }, []);

    const handlePointerDown = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.pointerType === "mouse") return;
        if (!enabled) return;
        if (isInteractiveTarget(event.target)) return;
        pointerIdRef.current = event.pointerId;
        startClientXRef.current = event.clientX;
        startClientYRef.current = event.clientY;
        baseIndexRef.current = currentIndexRef.current;
        draggingRef.current = false;
      },
      [enabled, isInteractiveTarget],
    );

    const handlePointerMove = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.pointerType === "mouse") return;
        if (pointerIdRef.current !== event.pointerId) return;
        if (startClientXRef.current === null || startClientYRef.current === null)
          return;
        if (width === 0) return;

        const dx = event.clientX - startClientXRef.current;
        const dy = event.clientY - startClientYRef.current;

        // Decide gesture ownership lazily. If the move is clearly vertical,
        // abandon carousel paging — parent will handle vertical gestures
        // (memory panel / AI summary) once pointerUp fires.
        if (
          !draggingRef.current &&
          Math.abs(dy) > Math.abs(dx) * 1.25 &&
          Math.abs(dy) > 12
        ) {
          pointerIdRef.current = null;
          startClientXRef.current = null;
          startClientYRef.current = null;
          return;
        }

        if (!draggingRef.current && Math.abs(dx) > 6) {
          draggingRef.current = true;
          onDragChange?.(true);
        }
        if (!draggingRef.current) return;

        // Clamp deltaX so we don't pull past the ends.
        const baseIdx = baseIndexRef.current;
        let clampedDx = dx;
        if (baseIdx <= 0 && clampedDx > 0) clampedDx = Math.min(clampedDx, 24);
        if (baseIdx >= pages.length - 1 && clampedDx < 0)
          clampedDx = Math.max(clampedDx, -24);

        applyTransform(baseIdx, clampedDx, false);
        onDrag?.(clampedDx);
      },
      [applyTransform, onDrag, onDragChange, pages.length, width],
    );

    const handlePointerUp = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.pointerType === "mouse") return;
        if (pointerIdRef.current !== event.pointerId) return;
        const startX = startClientXRef.current;
        const pointerId = pointerIdRef.current;
        pointerIdRef.current = null;
        startClientXRef.current = null;
        startClientYRef.current = null;

        if (startX === null) return;
        if (!draggingRef.current) {
          draggingRef.current = false;
          onDragChange?.(false);
          return;
        }
        draggingRef.current = false;
        onDragChange?.(false);

        const dx = event.clientX - startX;
        const ratio = Math.abs(dx) / Math.max(width, 1);
        const shouldCommit =
          ratio >= SWIPE_COMMIT_RATIO || Math.abs(dx) >= SWIPE_COMMIT_MIN_PX;

        const baseIdx = baseIndexRef.current;
        let targetIdx = baseIdx;
        if (shouldCommit) {
          if (dx < 0 && baseIdx < pages.length - 1) targetIdx = baseIdx + 1;
          else if (dx > 0 && baseIdx > 0) targetIdx = baseIdx - 1;
        }

        // Suppress the synthetic click that follows a drag on iOS Safari.
        suppressClickUntilRef.current = Date.now() + 400;

        void (async () => {
          await animateTo(targetIdx);
          // Post-snap: commit to store via parent callback.
          currentIndexRef.current = targetIdx;
          setCurrentIndex(targetIdx);
          onPageSettled(targetIdx);
        })();
        // Mark consumed so the parent's vertical/carousel gating treats
        // this pointer sequence as done.
        gestureConsumedExternal(pointerId);
      },
      [animateTo, onDragChange, onPageSettled, pages.length, width],
    );

    const handlePointerCancel = useCallback(
      (event: React.PointerEvent<HTMLDivElement>) => {
        if (pointerIdRef.current !== event.pointerId) return;
        pointerIdRef.current = null;
        startClientXRef.current = null;
        startClientYRef.current = null;
        if (draggingRef.current) {
          draggingRef.current = false;
          onDragChange?.(false);
          // Snap back to base index.
          void animateTo(baseIndexRef.current).then(() => {
            currentIndexRef.current = baseIndexRef.current;
            setCurrentIndex(baseIndexRef.current);
            onPageSettled(baseIndexRef.current);
          });
        }
      },
      [animateTo, onDragChange, onPageSettled],
    );

    // The carousel emits its own "gesture consumed" via a DOM event the
    // parent listens for. We avoid leaking internal ref shape.
    const gestureConsumedExternal = (_pointerId: number) => {
      // Intentionally empty — the parent (WallpaperStage) already gates
      // carousel/vertical decisions through pointerId-aware logic and does
      // not need a notification. Kept for symmetry with future extensions.
    };

    // Suppress click after a drag.
    useEffect(() => {
      const handler = (e: MouseEvent) => {
        if (Date.now() < suppressClickUntilRef.current) {
          e.stopPropagation();
          e.preventDefault();
        }
      };
      window.addEventListener("click", handler, true);
      return () => window.removeEventListener("click", handler, true);
    }, []);

    const pageNodes = useMemo(() => {
      return pages.map((page, index) => (
        <div
          key={page.key}
          data-carousel-page-index={index}
          className="shrink-0 h-full w-full"
          style={{ flex: "0 0 100%" }}
        >
          <div
            className={[
              "h-full w-full bg-cover bg-center bg-no-repeat",
              pageClassName ?? "",
            ].join(" ")}
            style={{
              backgroundImage: `url(${page.imageUrl})`,
            }}
            aria-hidden={index !== currentIndex}
          />
        </div>
      ));
    }, [pages, pageClassName, currentIndex]);

    return (
      <div
        ref={viewportRef}
        className="absolute inset-0 overflow-hidden"
        onPointerDownCapture={handlePointerDown}
        onPointerMoveCapture={handlePointerMove}
        onPointerUpCapture={handlePointerUp}
        onPointerCancelCapture={handlePointerCancel}
      >
        <div
          ref={trackRef}
          className="flex h-full"
          style={{
            width: width > 0 ? `${pages.length * 100}%` : undefined,
            willChange: "transform",
            touchAction: "pan-y",
          }}
        >
          {pageNodes}
        </div>
      </div>
    );
  },
);