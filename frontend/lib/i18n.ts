/**
 * i18n — type-safe flat key translation system.
 *
 * All user-visible strings are defined here.
 * Components use `useI18n().t(key)` to resolve the current language.
 */

import { useCallback, useMemo } from "react";
import { useAppPreferencesStore, type AppLanguage } from "./hooks/useAppPreferencesStore";
import { TODAY_INDEX } from "./hooks/useSceneStore";

// ── Key types ────────────────────────────────────────────────────────────────

export type I18nKey =
  // common
  | "common.back"
  | "common.settings"
  | "common.retry"
  | "common.loading"
  | "common.close"
  // profile (ProfileSetupScreen)
  | "profile.hello"
  | "profile.whoAreYou"
  | "profile.olderAdults"
  | "profile.adultChildren"
  | "profile.createLink"
  | "profile.saving"
  | "profile.join"
  | "profile.invitePlaceholder"
  | "profile.errorCreate"
  | "profile.errorJoin"
  | "profile.stepLabel"
  // pairing (PairingScreen)
  | "pairing.hello"
  | "pairing.title"
  | "pairing.shareLine1"
  | "pairing.shareLine2"
  | "pairing.waiting"
  | "pairing.errorCheck"
  | "pairing.errorLost"
  | "pairing.stepLabel"
  // settings (ProfileSettingsScreen)
  | "settings.title"
  | "settings.mode"
  | "settings.olderAdults"
  | "settings.adultChildren"
  | "settings.language"
  | "settings.font"
  | "settings.fontSize"
  | "settings.fontBold"
  | "settings.fontSizeLabel"
  | "settings.fontBoldLabel"
  | "settings.previewHello"
  | "settings.previewNihao"
  | "settings.backLabel"
  // photo (PhotoUploadScreen)
  | "photo.title"
  | "photo.chooseSource"
  | "photo.takePhoto"
  | "photo.useCamera"
  | "photo.uploadDevice"
  | "photo.chooseFiles"
  | "photo.closePicker"
  | "photo.generatePortait"
  | "photo.generating"
  | "photo.preferredName"
  | "photo.portraitReady"
  | "photo.otherReady"
  | "photo.otherWaiting"
  | "photo.errorRefresh"
  | "photo.errorName"
  | "photo.errorSession"
  | "photo.errorGenerate"
  | "photo.stepLabel"
  | "photo.backLabel"
  | "photo.openSettings"
  | "photo.continue"
  | "photo.continueLoading"
  | "photo.addPhoto"
  | "photo.changePhoto"
  // connected (ConnectionSuccessScreen)
  | "connected.title"
  | "connected.loading"
  | "connected.errorSession"
  | "connected.errorNotReady"
  | "connected.errorLoad"
  | "connected.tryAgain"
  | "connected.continue"
  | "connected.stepLabel"
  | "connected.backLabel"
  | "connected.openSettings"
  // prelude (PreludeStep)
  | "prelude.tapToSpeak"
  | "prelude.listening"
  | "prelude.creating"
  | "prelude.recordHint"
  | "prelude.startRecording"
  | "prelude.stopRecording"
  | "prelude.retryRecording"
  | "prelude.processing"
  // atmosphere (AtmosphereLayer)
  | "atmosphere.returnFull"
  | "atmosphere.enlargeUpper"
  | "atmosphere.enlargeLower"
  // chat (ChatOverlay + summary)
  | "chat.transcribing"
  | "chat.generating"
  | "chat.openSuggestions"
  | "chat.backToWallpaper"
  | "chat.closeSummary"
  | "chat.prevWallpaper"
  | "chat.nextWallpaper"
  | "chat.aiSuggestions"
  | "chat.touchSuggestion"
  | "chat.note"
  | "chat.originalHistory"
  | "chat.noHistory"
  | "chat.todaysConnection"
  | "chat.adultChild"
  | "chat.olderAdult"
  | "chat.hope"
  | "chat.abstract"
  | "chat.keywords"
  | "chat.voiceSec"
  | "chat.startRecording"
  | "chat.stopRecording"
  | "chat.recording"
  | "chat.processingVoice"
  // memory (MemoryAssetsPanel)
  | "memory.collection"
  | "memory.book"
  | "memory.shared"
  | "memory.recent"
  | "memory.itemCount"
  | "memory.refresh"
  | "memory.retry"
  | "memory.empty"
  | "memory.loading"
  | "memory.swipeDown"
  | "memory.close"
  // subject lift (useSubjectLift — user-facing error strings)
  | "subjectLift.extractionFailed"
  // voice edit (useWallpaperVoiceEditRecorder)
  | "voiceEdit.voiceUpdate"
  | "voiceEdit.tooShort"
  // wallpaper stage
  | "wallpaperStage.openRecentMemory"
  | "wallpaperStage.errorRelationship"
  | "wallpaperStage.errorLoadMemory";

// ── Dictionaries ────────────────────────────────────────────────────────────

const en: Record<I18nKey, string> = {
  // common
  "common.back": "Back",
  "common.settings": "Settings",
  "common.retry": "Retry",
  "common.loading": "Loading...",
  "common.close": "Close",
  // profile
  "profile.hello": "Hello",
  "profile.whoAreYou": "Who are you?",
  "profile.olderAdults": "Older Adults",
  "profile.adultChildren": "Adult Children",
  "profile.createLink": "Create Link",
  "profile.saving": "Saving...",
  "profile.join": "Join",
  "profile.invitePlaceholder": "4-digit code",
  "profile.errorCreate": "Unable to create your family link. Please try again.",
  "profile.errorJoin": "The code is invalid, expired, or belongs to the same role.",
  "profile.stepLabel": "Step {n} of 3",
  // pairing
  "pairing.hello": "Hello",
  "pairing.title": "Connect with your family",
  "pairing.shareLine1": "Share this four digit code with",
  "pairing.shareLine2": "the other family members.",
  "pairing.waiting": "Waiting for your family members to join in...",
  "pairing.errorCheck": "Unable to check the connection right now.",
  "pairing.errorLost": "Connection lost. Reconnecting...",
  "pairing.stepLabel": "Onboarding progress: step 1 of 3",
  // settings
  "settings.title": "Setting",
  "settings.mode": "Mode",
  "settings.olderAdults": "Older Adults",
  "settings.adultChildren": "Adult Children",
  "settings.language": "Language",
  "settings.font": "Font",
  "settings.fontSize": "Font Size",
  "settings.fontBold": "Font Bold",
  "settings.fontSizeLabel": "Big",
  "settings.fontBoldLabel": "Default",
  "settings.previewHello": "Hello",
  "settings.previewNihao": "你好",
  "settings.backLabel": "Back to profile setup",
  // photo
  "photo.title": "Profile",
  "photo.chooseSource": "Choose a photo source",
  "photo.takePhoto": "Take Photo",
  "photo.useCamera": "Use your camera",
  "photo.uploadDevice": "Upload from Device",
  "photo.chooseFiles": "Choose from photos or files",
  "photo.closePicker": "Close photo source picker",
  "photo.generatePortait": "Generate custom portrait",
  "photo.generating": "Generating portrait...",
  "photo.preferredName": "Preferred name",
  "photo.portraitReady": "Your AI portrait is ready",
  "photo.otherReady": "The other is ready",
  "photo.otherWaiting": "Waiting for the other",
  "photo.errorRefresh": "Unable to refresh your portrait right now.",
  "photo.errorName": "Enter your preferred name before generating a portrait.",
  "photo.errorSession": "Your family session is unavailable. Please reconnect first.",
  "photo.errorGenerate": "Portrait generation failed. Please try again.",
  "photo.stepLabel": "Onboarding progress: step 2 of 3",
  "photo.backLabel": "Return to the previous step",
  "photo.openSettings": "Open settings",
  "photo.continue": "Continue",
  "photo.continueLoading": "Opening the next step",
  "photo.addPhoto": "Add a portrait photo",
  "photo.changePhoto": "Choose a different portrait photo",
  // connected
  "connected.title": "Congrats!\nYou're connected!",
  "connected.loading": "Loading your connection...",
  "connected.errorSession": "Your family session could not be restored.",
  "connected.errorNotReady": "The two portraits are not ready yet.",
  "connected.errorLoad": "Unable to load your connected profiles.",
  "connected.tryAgain": "Try again",
  "connected.continue": "Continue to the next step",
  "connected.stepLabel": "Onboarding progress: step 3 of 3",
  "connected.backLabel": "Return to the previous step",
  "connected.openSettings": "Open settings",
  // prelude
  "prelude.tapToSpeak": "Tap to speak",
  "prelude.listening": "Listening...",
  "prelude.creating": "Creating...",
  "prelude.recordHint": "Record a message",
  "prelude.startRecording": "Start recording",
  "prelude.stopRecording": "Stop recording",
  "prelude.retryRecording": "Retry recording",
  "prelude.processing": "Processing",
  // atmosphere
  "atmosphere.returnFull": "Return to the full wallpaper",
  "atmosphere.enlargeUpper": "Enlarge the upper-right area",
  "atmosphere.enlargeLower": "Enlarge the lower-left area",
  // chat
  "chat.transcribing": "Recognizing speech...",
  "chat.generating": "Generating new wallpaper...",
  "chat.openSuggestions": "Open communication suggestions",
  "chat.backToWallpaper": "Back to wallpaper",
  "chat.closeSummary": "Close summary",
  "chat.prevWallpaper": "View previous wallpaper",
  "chat.nextWallpaper": "View next wallpaper",
  "chat.aiSuggestions": "AI Suggestions",
  "chat.touchSuggestion": "Tap for a suggestion",
  "chat.note": "Note",
  "chat.originalHistory": "Original Chat History",
  "chat.noHistory": "No conversation yet",
  "chat.todaysConnection": "Today's Connection",
  "chat.adultChild": "Adult child",
  "chat.olderAdult": "Older adult",
  "chat.hope": "Hope",
  "chat.abstract": "Abstract",
  "chat.keywords": "Keywords",
  "chat.voiceSec": "{n}s voice",
  "chat.startRecording": "Start recording",
  "chat.stopRecording": "Stop recording",
  "chat.recording": "Recording",
  "chat.processingVoice": "Creating your wallpaper...",
  // memory
  "memory.collection": "Your collection",
  "memory.book": "Memory Book",
  "memory.shared": "Your Shared Artifacts",
  "memory.recent": "Your Recent Memory",
  "memory.itemCount": "{count} items",
  "memory.refresh": "Refresh memory objects",
  "memory.retry": "Retry",
  "memory.empty": "No recurring memory objects yet.",
  "memory.loading": "Loading...",
  "memory.swipeDown": "Swipe down to return to the wallpaper",
  "memory.close": "Return to wallpaper",
  // subject lift
  "subjectLift.extractionFailed": "Failed to extract subject. Please try again.",
  // voice edit
  "voiceEdit.voiceUpdate": "Voice update",
  "voiceEdit.tooShort": "Recording too short, please retry.",
  // wallpaper stage
  "wallpaperStage.openRecentMemory": "Open recent memory objects",
  "wallpaperStage.errorRelationship": "Your family relationship is not available.",
  "wallpaperStage.errorLoadMemory": "Unable to load recent memory objects.",
};

const zh: Record<I18nKey, string> = {
  // common
  "common.back": "返回",
  "common.settings": "设置",
  "common.retry": "重试",
  "common.loading": "加载中...",
  "common.close": "关闭",
  // profile
  "profile.hello": "你好",
  "profile.whoAreYou": "你是谁？",
  "profile.olderAdults": "老年人",
  "profile.adultChildren": "成年子女",
  "profile.createLink": "创建链接",
  "profile.saving": "保存中...",
  "profile.join": "加入",
  "profile.invitePlaceholder": "4位验证码",
  "profile.errorCreate": "无法创建家庭链接，请重试。",
  "profile.errorJoin": "验证码无效、已过期或属于相同角色。",
  "profile.stepLabel": "第 {n} 步，共 3 步",
  // pairing
  "pairing.hello": "你好",
  "pairing.title": "与家人建立连接",
  "pairing.shareLine1": "将此四位验证码分享给",
  "pairing.shareLine2": "其他家庭成员。",
  "pairing.waiting": "等待家庭成员加入中...",
  "pairing.errorCheck": "暂时无法检查连接状态。",
  "pairing.errorLost": "连接已断开，正在重新连接...",
  "pairing.stepLabel": "引导进度：第 1 步，共 3 步",
  // settings
  "settings.title": "设置",
  "settings.mode": "模式",
  "settings.olderAdults": "老年人",
  "settings.adultChildren": "成年子女",
  "settings.language": "语言",
  "settings.font": "字体",
  "settings.fontSize": "字体大小",
  "settings.fontBold": "字体粗细",
  "settings.fontSizeLabel": "大",
  "settings.fontBoldLabel": "默认",
  "settings.previewHello": "Hello",
  "settings.previewNihao": "你好",
  "settings.backLabel": "返回身份设置",
  // photo
  "photo.title": "个人资料",
  "photo.chooseSource": "选择图片来源",
  "photo.takePhoto": "拍照",
  "photo.useCamera": "使用相机",
  "photo.uploadDevice": "从设备上传",
  "photo.chooseFiles": "从照片或文件中选择",
  "photo.closePicker": "关闭图片来源选择",
  "photo.generatePortait": "生成专属头像",
  "photo.generating": "正在生成头像...",
  "photo.preferredName": "昵称",
  "photo.portraitReady": "您的 AI 头像已就绪",
  "photo.otherReady": "对方已就绪",
  "photo.otherWaiting": "等待对方",
  "photo.errorRefresh": "暂时无法刷新头像状态。",
  "photo.errorName": "请先输入昵称再生成头像。",
  "photo.errorSession": "家庭会话不可用，请重新连接。",
  "photo.errorGenerate": "头像生成失败，请重试。",
  "photo.stepLabel": "引导进度：第 2 步，共 3 步",
  "photo.backLabel": "返回上一步",
  "photo.openSettings": "打开设置",
  "photo.continue": "继续",
  "photo.continueLoading": "正在进入下一步",
  "photo.addPhoto": "添加头像照片",
  "photo.changePhoto": "更换头像照片",
  // connected
  "connected.title": "恭喜！\n你们已成功连接！",
  "connected.loading": "正在加载连接信息...",
  "connected.errorSession": "无法恢复您的家庭会话。",
  "connected.errorNotReady": "双方头像尚未就绪。",
  "connected.errorLoad": "无法加载已连接的资料。",
  "connected.tryAgain": "重试",
  "connected.continue": "继续到下一步",
  "connected.stepLabel": "引导进度：第 3 步，共 3 步",
  "connected.backLabel": "返回上一步",
  "connected.openSettings": "打开设置",
  // prelude
  "prelude.tapToSpeak": "点击说话",
  "prelude.listening": "正在聆听...",
  "prelude.creating": "生成中...",
  "prelude.recordHint": "录制一条消息",
  "prelude.startRecording": "开始录音",
  "prelude.stopRecording": "停止录音",
  "prelude.retryRecording": "重试录音",
  "prelude.processing": "处理中",
  // atmosphere
  "atmosphere.returnFull": "返回完整壁纸",
  "atmosphere.enlargeUpper": "放大右上区域",
  "atmosphere.enlargeLower": "放大左下区域",
  // chat
  "chat.transcribing": "正在识别语音…",
  "chat.generating": "正在生成新壁纸…",
  "chat.openSuggestions": "打开沟通建议",
  "chat.backToWallpaper": "返回壁纸",
  "chat.closeSummary": "关闭摘要",
  "chat.prevWallpaper": "查看上一张壁纸",
  "chat.nextWallpaper": "查看下一张壁纸",
  "chat.aiSuggestions": "AI 建议",
  "chat.touchSuggestion": "轻触建议",
  "chat.note": "注意",
  "chat.originalHistory": "原始聊天记录",
  "chat.noHistory": "暂无对话记录",
  "chat.todaysConnection": "今日连线",
  "chat.adultChild": "成年子女",
  "chat.olderAdult": "老年人",
  "chat.hope": "希望",
  "chat.abstract": "摘要",
  "chat.keywords": "关键词",
  "chat.voiceSec": "{n}秒语音",
  "chat.startRecording": "开始录音",
  "chat.stopRecording": "停止录音",
  "chat.recording": "录音中",
  "chat.processingVoice": "正在生成壁纸…",
  // memory
  "memory.collection": "你的收藏",
  "memory.book": "记忆之书",
  "memory.shared": "你们的共同记忆",
  "memory.recent": "近期记忆",
  "memory.itemCount": "{count} 件",
  "memory.refresh": "刷新记忆物件",
  "memory.retry": "重试",
  "memory.empty": "暂无重复出现的记忆物件。",
  "memory.loading": "加载中...",
  "memory.swipeDown": "向下滑动返回壁纸",
  "memory.close": "返回壁纸",
  // subject lift
  "subjectLift.extractionFailed": "抠图失败，请重试。",
  // voice edit
  "voiceEdit.voiceUpdate": "语音更新",
  "voiceEdit.tooShort": "录音过短，请重试。",
  // wallpaper stage
  "wallpaperStage.openRecentMemory": "打开近期记忆",
  "wallpaperStage.errorRelationship": "家庭关系不可用。",
  "wallpaperStage.errorLoadMemory": "无法加载近期记忆物件。",
};

export const dictionaries: Record<AppLanguage, Record<I18nKey, string>> = { en, zh };

// ── Simple variable replacement ──────────────────────────────────────────────

function interpolate(text: string, variables?: Record<string, string | number>): string {
  if (!variables) return text;
  return text.replace(/\{(\w+)\}/g, (_, key) => {
    const val = variables[key];
    return val !== undefined ? String(val) : `{${key}}`;
  });
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export type UseI18nReturn = {
  language: AppLanguage;
  setLanguage: (l: AppLanguage) => void;
  locale: string;
  t: (key: I18nKey, variables?: Record<string, string | number>) => string;
};

export function useI18n(): UseI18nReturn {
  const language = useAppPreferencesStore((s) => s.language);
  const setLanguage = useAppPreferencesStore((s) => s.setLanguage);

  const t = useCallback(
    (key: I18nKey, variables?: Record<string, string | number>): string => {
      const dict = dictionaries[language];
      const text = dict[key] ?? dictionaries.en[key] ?? key;
      return interpolate(text, variables);
    },
    [language],
  );

  const locale = useMemo(
    () => (language === "en" ? "en-US" : "zh-CN"),
    [language],
  );

  return { language, setLanguage, locale, t };
}

// ── Date formatting ───────────────────────────────────────────────────────────

export function formatLongDate(dayIndex: number, locale: string): string {
  const today = new Date();
  const target = new Date(today);
  target.setDate(today.getDate() - (TODAY_INDEX - dayIndex));

  if (locale === "zh-CN") {
    const dayNames = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
    const month = target.getMonth() + 1;
    const day = target.getDate();
    const weekday = dayNames[target.getDay()];
    return `${month}月${day}日 ${weekday}`;
  }

  // en-US
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(target);
}

export function formatSummaryDate(dayIndex: number, locale: string): string {
  const today = new Date();
  const target = new Date(today);
  target.setDate(today.getDate() - (TODAY_INDEX - dayIndex));

  if (locale === "zh-CN") {
    const dayNames = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
    const month = target.getMonth() + 1;
    const day = target.getDate();
    const weekday = dayNames[target.getDay()];
    return `${month}月${day}日 ${weekday}`;
  }

  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(target);
}
