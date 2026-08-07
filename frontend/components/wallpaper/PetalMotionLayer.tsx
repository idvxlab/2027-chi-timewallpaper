"use client";

import type { CSSProperties } from "react";
import { useSceneStore } from "@/lib/hooks/useSceneStore";

const PETAL_SOURCES = [
  "/motion/peach-petal-heart.png",
  "/motion/peach-petal-curved.png",
  "/motion/peach-petal-folded.png",
] as const;

type Petal = {
  sourceIndex: number;
  size: number;
  delay: number;
  duration: number;
  laneX: number;
  laneY: number;
  rotation: number;
};

// Fixed values keep the first render deterministic while still making the
// petals look organic. Each lane is a small offset from the visible stone
// path, which runs from the bottom-right foreground toward the upper-left.
const PETALS: Petal[] = [
  {
    sourceIndex: 0,
    size: 27,
    delay: 0,
    duration: 10.8,
    laneX: 0,
    laneY: 0,
    rotation: 540,
  },
  {
    sourceIndex: 1,
    size: 22,
    delay: 1.6,
    duration: 11.6,
    laneX: 4.2,
    laneY: -1.1,
    rotation: -620,
  },
  {
    sourceIndex: 2,
    size: 30,
    delay: 3.2,
    duration: 10.4,
    laneX: -4.8,
    laneY: 1.3,
    rotation: 580,
  },
  {
    sourceIndex: 0,
    size: 20,
    delay: 4.8,
    duration: 11.9,
    laneX: 2.8,
    laneY: 1.8,
    rotation: -560,
  },
  {
    sourceIndex: 0,
    size: 23,
    delay: 6.4,
    duration: 11.2,
    laneX: -2.4,
    laneY: -1.8,
    rotation: 650,
  },
  {
    sourceIndex: 2,
    size: 19,
    delay: 8,
    duration: 10.6,
    laneX: 5.8,
    laneY: 0.7,
    rotation: -680,
  },
  {
    sourceIndex: 1,
    size: 26,
    delay: 9.6,
    duration: 11.4,
    laneX: -6.2,
    laneY: 0,
    rotation: 600,
  },
  {
    sourceIndex: 1,
    size: 21,
    delay: 2.4,
    duration: 12.1,
    laneX: 7.2,
    laneY: 1.6,
    rotation: -590,
  },
  {
    sourceIndex: 2,
    size: 24,
    delay: 7.2,
    duration: 11,
    laneX: -7.4,
    laneY: -0.8,
    rotation: 630,
  },
];

type PetalStyle = CSSProperties & {
  "--lane-x": string;
  "--lane-y": string;
  "--petal-rotation-1": string;
  "--petal-rotation-2": string;
  "--petal-rotation-3": string;
  "--petal-rotation-end": string;
};

type PetalMotionLayerProps = {
  hidden?: boolean;
  motionPaused?: boolean;
};

export function PetalMotionLayer({
  hidden = false,
  motionPaused = false,
}: PetalMotionLayerProps) {
  const uiMode = useSceneStore((state) => state.uiMode);

  if (uiMode !== "wallpaper") return null;

  return (
    <div
      className="petal-motion-layer pointer-events-none absolute inset-0 z-20 overflow-hidden"
      data-motion-paused={motionPaused ? "true" : "false"}
      aria-hidden="true"
      style={{ opacity: hidden ? 0 : 1 }}
    >
      <div className="absolute inset-0 overflow-hidden">
        {PETALS.map((petal, index) => {
          const style: PetalStyle = {
            width: petal.size,
            height: petal.size,
            animationDelay: `${petal.delay}s`,
            animationDuration: `${petal.duration}s`,
            "--lane-x": `${petal.laneX}%`,
            "--lane-y": `${petal.laneY}%`,
            "--petal-rotation-1": `${petal.rotation * 0.24}deg`,
            "--petal-rotation-2": `${petal.rotation * 0.48}deg`,
            "--petal-rotation-3": `${petal.rotation * 0.72}deg`,
            "--petal-rotation-end": `${petal.rotation}deg`,
          };

          return (
            <img
              key={`${petal.sourceIndex}-${index}`}
              src={PETAL_SOURCES[petal.sourceIndex]}
              alt=""
              draggable={false}
              className="petal-sprite absolute select-none object-contain"
              style={{
                ...style,
                animationPlayState: motionPaused ? "paused" : "running",
              }}
            />
          );
        })}
      </div>

      <style jsx>{`
        .petal-sprite {
          opacity: 0;
          filter: drop-shadow(0 1.5px 1.5px rgba(104, 77, 52, 0.2));
          will-change: transform, opacity;
          animation-name: petal-float;
          animation-timing-function: ease-in-out;
          animation-iteration-count: infinite;
        }

        .petal-motion-layer {
          transition: opacity 360ms ease-in-out;
        }

        @keyframes petal-float {
          0% {
            left: calc(91% + var(--lane-x));
            top: calc(94% + var(--lane-y));
            opacity: 0;
            transform: translate3d(0, 0, 0) rotate(0deg) scale(0.66);
          }
          8% {
            opacity: 0.82;
          }
          24% {
            left: calc(78% + var(--lane-x));
            top: calc(84% + var(--lane-y));
            opacity: 0.9;
            transform: translate3d(13px, -8px, 0)
              rotate(var(--petal-rotation-1)) scale(0.9);
          }
          48% {
            left: calc(60% + var(--lane-x));
            top: calc(69% + var(--lane-y));
            opacity: 0.88;
            transform: translate3d(-12px, 6px, 0)
              rotate(var(--petal-rotation-2)) scale(1);
          }
          72% {
            left: calc(39% + var(--lane-x));
            top: calc(52% + var(--lane-y));
            opacity: 0.78;
            transform: translate3d(14px, -7px, 0)
              rotate(var(--petal-rotation-3)) scale(0.9);
          }
          92% {
            opacity: 0.52;
          }
          100% {
            left: calc(20% + var(--lane-x));
            top: calc(36% + var(--lane-y));
            opacity: 0;
            transform: translate3d(-10px, 4px, 0)
              rotate(var(--petal-rotation-end)) scale(0.72);
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .petal-motion-layer {
            display: none;
          }
        }
      `}</style>
    </div>
  );
}
