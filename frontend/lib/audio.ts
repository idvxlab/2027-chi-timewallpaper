let unlocked = false;
const cache = new Map<string, HTMLAudioElement>();

export async function ensureUnlocked(): Promise<void> {
  if (unlocked) return;
  const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
  if (ctx.state === "suspended") await ctx.resume();
  unlocked = true;
}

export function playCue(src: string, volume = 0.6): void {
  if (!unlocked) return;
  let audio = cache.get(src);
  if (!audio) {
    audio = new Audio(src);
    audio.preload = "auto";
    cache.set(src, audio);
  }
  audio.currentTime = 0;
  audio.volume = volume;
  void audio.play().catch(() => {
    /* 用户尚未交互,忽略 */
  });
}

export function startAmbient(src: string): void {
  if (!unlocked) return;
  let audio = cache.get(src);
  if (!audio) {
    audio = new Audio(src);
    audio.loop = true;
    audio.volume = 0.3;
    cache.set(src, audio);
  }
  void audio.play().catch(() => undefined);
}

export function stopAmbient(src: string): void {
  const audio = cache.get(src);
  if (audio) {
    audio.pause();
    audio.currentTime = 0;
  }
}
