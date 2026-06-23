"use client";

import { useState, useEffect } from "react";
import { useRecorder } from "@/lib/hooks/useRecorder";
import { useSceneStore } from "@/lib/hooks/useSceneStore";

const DAY_LABELS = ["前天", "昨天", "今天"] as const;

// ─── Hardcoded mock messages (no store dependency) ─────────────────────────────

type Msg = { id: string; from: "child" | "elder"; text: string; time: string };

const MOCK_MESSAGES: Record<string, Msg[]> = {
  前天: [
    {
      id: "1-1",
      from: "child",
      text: "奶奶，昨天晚上梦到你了，梦见我们一起去公园玩。",
      time: "08:32",
    },
    {
      id: "1-2",
      from: "elder",
      text: "宝贝，梦里的公园是不是很漂亮呀？奶奶也想你。",
      time: "08:45",
    },
    {
      id: "1-3",
      from: "child",
      text: "是的！奶奶你要注意身体，天冷了要多穿衣服。",
      time: "09:01",
    },
    {
      id: "1-4",
      from: "elder",
      text: "奶奶知道了，乖孩子，你在学校要好好吃饭啊。",
      time: "09:15",
    },
  ],
  昨天: [
    {
      id: "2-1",
      from: "child",
      text: "奶奶，今天妈妈做了红烧肉，特别香！我给你留了一块。",
      time: "12:20",
    },
    {
      id: "2-2",
      from: "elder",
      text: "哎呀，我家宝贝真乖，记得给奶奶留好吃的。",
      time: "12:35",
    },
    {
      id: "2-3",
      from: "child",
      text: "下周我就回去看奶奶啦，带你吃大餐！",
      time: "13:02",
    },
    {
      id: "2-4",
      from: "elder",
      text: "太好了，奶奶每天都在数着日子等你回来。",
      time: "13:18",
    },
    {
      id: "2-5",
      from: "child",
      text: "奶奶我这次考试考了95分，老师表扬我了！",
      time: "15:44",
    },
    {
      id: "2-6",
      from: "elder",
      text: "太棒了！奶奶就知道你最聪明，继续加油啊。",
      time: "16:02",
    },
  ],
  今天: [
    {
      id: "3-1",
      from: "child",
      text: "奶奶，今天天气真好呀，你那边怎么样？",
      time: "09:10",
    },
    {
      id: "3-2",
      from: "elder",
      text: "宝贝乖，奶奶这边也挺好的，就是有点想你。",
      time: "09:25",
    },
    {
      id: "3-3",
      from: "child",
      text: "我下周就回来看你啦！给你带好吃的。",
      time: "09:31",
    },
    {
      id: "3-4",
      from: "elder",
      text: "太好了，奶奶等着你，记得多穿点衣服。",
      time: "09:40",
    },
    {
      id: "3-5",
      from: "child",
      text: "知道啦奶奶，那我先去上课了，晚上再给你打电话。",
      time: "10:05",
    },
    {
      id: "3-6",
      from: "elder",
      text: "好，去吧，好好学习，奶奶爱你。",
      time: "10:12",
    },
  ],
};

// ─── TextBubble ───────────────────────────────────────────────────────────────

function TextBubble({ msg }: { msg: Msg }) {
  const isMine = msg.from === "child";
  return (
    <div
      className={`flex items-end gap-2 mb-3 ${isMine ? "flex-row" : "flex-row-reverse"}`}
    >
      <div
        className={`max-w-[72%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed ${
          isMine
            ? "bg-[#95EC69] text-slate-800 rounded-br-sm"
            : "bg-white text-slate-800 rounded-bl-sm border border-slate-200"
        }`}
      >
        {msg.text}
      </div>
      <span className="text-xs opacity-40 pb-0.5 text-slate-400">
        {msg.time}
      </span>
    </div>
  );
}

// ─── ChatOverlay ──────────────────────────────────────────────────────────────

export function ChatOverlay() {
  const uiMode = useSceneStore((s) => s.uiMode);
  const toggleUiMode = useSceneStore((s) => s.toggleUiMode);
  const currentDayIndex = useSceneStore((s) => s.currentDayIndex);
  const shiftDay = useSceneStore((s) => s.shiftDay);
  const messagesByDay = useSceneStore((s) => s.messagesByDay);

  const { status, toggle, elapsedSec } = useRecorder();


  const isWhite = uiMode === "white";
  const dayLabel = DAY_LABELS[currentDayIndex];
  const messages = (messagesByDay[dayLabel] ?? []).map((msg) => ({
    id: msg.id,
    from: msg.from,
    text: msg.text || (msg.type === "voice_message" ? `语音留言 ${msg.durationSec}s` : ""),
    time: new Date(msg.timestamp).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }),
  }));

  function handleRecord() {
    toggle("child");
  }

  return (
    <>
      {/* ── White mode: WeChat-style read-only chat ── */}
      {isWhite && (
        <div className="absolute inset-0 z-40 flex flex-col bg-white">
          {/* Top bar */}
          <div className="flex items-center justify-center py-3 px-4 border-b border-slate-100 bg-white shadow-sm shrink-0">
            <span className="text-base font-semibold text-slate-800">
              {dayLabel}
            </span>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-4">
            <div>
              {messages.map((msg) => (
                <TextBubble key={msg.id} msg={msg} />
              ))}
            </div>
          </div>

          {/* Bottom hint */}
          <div className="shrink-0 px-4 py-3 border-t border-slate-100 bg-white">
            <p className="text-center text-xs text-slate-400">
              切换到「今天」可发送语音留言
            </p>
          </div>
        </div>
      )}

      {/* ── Wallpaper mode: elder hotspot recording overlay ── */}
      {!isWhite && (
        <div className="absolute inset-0 z-40 bg-transparent">
          {currentDayIndex === 2 && status !== "stopping" && (
            <button
              className="absolute left-[8%] bottom-[8%] z-50 h-[44%] w-[42%] rounded-[32px] bg-transparent transition-colors hover:bg-white/5 active:bg-white/10"
              onClick={handleRecord}
              aria-label={status === "recording" ? "结束录音" : "点击老人开始录音"}
            />
          )}
          {status !== "idle" && (
            <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 py-2 bg-black/30 backdrop-blur-sm text-white text-sm font-medium">
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              {status === "stopping"
                ? "正在保存…"
                : `录音中 ${elapsedSec}s，再点老人停止`}
            </div>
          )}
        </div>
      )}

      {status === "idle" && (
        <>
          <button
            className="absolute left-0 top-16 bottom-16 z-50 w-16 bg-transparent"
            onClick={() => shiftDay(-1)}
            disabled={currentDayIndex === 0}
            aria-label="切换到前一天"
          />
          <button
            className="absolute right-0 top-16 bottom-16 z-50 w-16 bg-transparent"
            onClick={() => shiftDay(1)}
            disabled={currentDayIndex === 2}
            aria-label="切换到后一天"
          />
        </>
      )}

      {/* Top-right invisible chat toggle */}
      <button
        onClick={toggleUiMode}
        className="absolute right-0 top-0 z-50 h-20 w-20 bg-transparent"
        aria-label={isWhite ? "返回壁纸" : "打开聊天记录"}
      />

      {/* Day label */}
      <div
        className={`absolute top-4 left-1/2 z-50 flex items-center px-4 py-2 rounded-full shadow-md pointer-events-none select-none ${
          isWhite
            ? "bg-white border border-slate-200"
            : "bg-black/30 backdrop-blur-sm"
        }`}
        style={{ transform: "translateX(-50%)" }}
      >
        <span
          className={`text-sm font-semibold min-w-[2.5rem] text-center ${isWhite ? "text-slate-700" : "text-white"}`}
        >
          {dayLabel}
        </span>
      </div>
    </>
  );
}
