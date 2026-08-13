"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  PANORAMA_HOLD_DELAY_MS,
  panoramaPositionAtElapsed,
  type PanoramaDirection,
} from "@/lib/panoramaCamera";

const MOVE_TOLERANCE_PX = 14;
const POSITION_EPSILON = 0.0001;
const UPDATE_INTERVAL_MS = 32;

type PanoramaHoldControlsProps = {
  position: number;
  disabled?: boolean;
  onPositionChange: (position: number) => void;
  onShortPress?: (direction: PanoramaDirection) => void;
};

export function PanoramaHoldControls({
  position,
  disabled = false,
  onPositionChange,
  onShortPress,
}: PanoramaHoldControlsProps) {
  const [holding, setHolding] = useState<PanoramaDirection | null>(null);
  const [moving, setMoving] = useState(false);
  const frameRef = useRef<number | null>(null);
  const startPointRef = useRef<{ x: number; y: number } | null>(null);
  const startTimeRef = useRef(0);
  const startPositionRef = useRef(position);
  const lastUpdateTimeRef = useRef(0);
  const directionRef = useRef<PanoramaDirection | null>(null);

  const cancelHold = useCallback(() => {
    console.log("[PANORAMA_POINTER_UP_OR_CANCEL]", { direction: directionRef.current });
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    startPointRef.current = null;
    directionRef.current = null;
    setHolding(null);
    setMoving(false);
  }, []);

  useEffect(() => cancelHold, [cancelHold]);
  useEffect(() => {
    if (disabled) cancelHold();
  }, [cancelHold, disabled]);

  const startHold = useCallback(
    (
      direction: PanoramaDirection,
      event: React.PointerEvent<HTMLButtonElement>,
    ) => {
      console.log("[PANORAMA_POINTER_DOWN]", {
        direction,
        disabled,
        position,
        isPrimary: event.isPrimary,
      });
      const alreadyAtEdge =
        direction === "left"
          ? position <= POSITION_EPSILON
          : position >= 1 - POSITION_EPSILON;
      if (
        disabled ||
        (alreadyAtEdge && !onShortPress) ||
        !event.isPrimary
      ) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture(event.pointerId);
      cancelHold();
      startPointRef.current = { x: event.clientX, y: event.clientY };
      startTimeRef.current = performance.now();
      startPositionRef.current = position;
      lastUpdateTimeRef.current = 0;
      directionRef.current = direction;
      setHolding(direction);

      const advance = (now: number) => {
        if (directionRef.current !== direction) return;
        const elapsedMs = now - startTimeRef.current;

        if (elapsedMs >= PANORAMA_HOLD_DELAY_MS) {
          setMoving(true);
          if (
            lastUpdateTimeRef.current === 0 ||
            now - lastUpdateTimeRef.current >= UPDATE_INTERVAL_MS
          ) {
            const nextPosition = panoramaPositionAtElapsed({
              startPosition: startPositionRef.current,
              direction,
              elapsedMs,
            });
            lastUpdateTimeRef.current = now;
            onPositionChange(nextPosition);

            const reachedEdge =
              nextPosition <= POSITION_EPSILON ||
              nextPosition >= 1 - POSITION_EPSILON;
            if (reachedEdge) {
              cancelHold();
              return;
            }
          }
        }

        frameRef.current = requestAnimationFrame(advance);
      };
      frameRef.current = requestAnimationFrame(advance);
    },
    [cancelHold, disabled, onPositionChange, onShortPress, position],
  );

  const handleMove = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      const start = startPointRef.current;
      if (!start) return;
      if (
        Math.hypot(event.clientX - start.x, event.clientY - start.y) >
        MOVE_TOLERANCE_PX
      ) {
        cancelHold();
      }
    },
    [cancelHold],
  );

  const finishHold = useCallback(() => {
    const direction = directionRef.current;
    const elapsedMs = performance.now() - startTimeRef.current;
    const isShortPress =
      direction !== null && elapsedMs < PANORAMA_HOLD_DELAY_MS;
    cancelHold();
    if (isShortPress) onShortPress?.(direction);
  }, [cancelHold, onShortPress]);

  const leftAtEdge = position <= POSITION_EPSILON;
  const rightAtEdge = position >= 1 - POSITION_EPSILON;

  return (
    <>
      <HoldEdge
        direction="left"
        active={holding === "left"}
        moving={holding === "left" && moving}
        disabled={disabled || (leftAtEdge && !onShortPress)}
        onPointerDown={(event) => startHold("left", event)}
        onPointerMove={handleMove}
        onPointerUp={finishHold}
        onPointerCancel={cancelHold}
      />
      <HoldEdge
        direction="right"
        active={holding === "right"}
        moving={holding === "right" && moving}
        disabled={disabled || (rightAtEdge && !onShortPress)}
        onPointerDown={(event) => startHold("right", event)}
        onPointerMove={handleMove}
        onPointerUp={finishHold}
        onPointerCancel={cancelHold}
      />
      <style jsx global>{`
        @keyframes panorama-hold-progress {
          from {
            stroke-dashoffset: 100;
          }
          to {
            stroke-dashoffset: 0;
          }
        }
        @keyframes panorama-moving-pulse {
          0%,
          100% {
            transform: scale(1);
          }
          50% {
            transform: scale(1.08);
          }
        }
      `}</style>
    </>
  );
}

function HoldEdge({
  direction,
  active,
  moving,
  disabled,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
}: {
  direction: "left" | "right";
  active: boolean;
  moving: boolean;
  disabled: boolean;
  onPointerDown: (event: React.PointerEvent<HTMLButtonElement>) => void;
  onPointerMove: (event: React.PointerEvent<HTMLButtonElement>) => void;
  onPointerUp: () => void;
  onPointerCancel: () => void;
}) {
  const isLeft = direction === "left";
  return (
    <button
      type="button"
      data-panorama-hold-control={direction}
      aria-label={`长按一秒后缓慢查看${isLeft ? "左侧" : "右侧"}扩展画面`}
      disabled={disabled}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onLostPointerCapture={onPointerCancel}
      onContextMenu={(event) => event.preventDefault()}
      className={`absolute bottom-20 top-20 z-[55] flex min-w-[56px] min-h-[56px] max-w-[92px] items-center justify-center bg-transparent transition-opacity ${
        isLeft ? "left-0" : "right-0"
      } w-[12%] ${disabled ? "opacity-0" : "opacity-40 hover:opacity-70"}`}
      style={{ touchAction: "none", WebkitTouchCallout: "none" }}
    >
      <span
        className="relative flex h-14 w-14 items-center justify-center rounded-full bg-black/15 text-3xl text-white backdrop-blur-[2px]"
        style={
          moving
            ? { animation: "panorama-moving-pulse 900ms ease-in-out infinite" }
            : undefined
        }
      >
        {isLeft ? "‹" : "›"}
        {active && !moving ? (
          <svg
            className="pointer-events-none absolute inset-0 -rotate-90"
            viewBox="0 0 36 36"
            aria-hidden="true"
          >
            <circle
              cx="18"
              cy="18"
              r="15.9"
              fill="none"
              stroke="rgba(255,255,255,.95)"
              strokeWidth="2"
              strokeDasharray="100"
              strokeDashoffset="100"
              style={{
                animation:
                  `panorama-hold-progress ${PANORAMA_HOLD_DELAY_MS}ms linear forwards`,
              }}
            />
          </svg>
        ) : null}
      </span>
    </button>
  );
}
