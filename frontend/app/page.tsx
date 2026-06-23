import { IpadFrame } from "@/components/shared/IpadFrame";
import { WallpaperStage } from "@/components/wallpaper/WallpaperStage";
import { ChatOverlay } from "@/components/wallpaper/ChatOverlay";
import { DemoSwitcher } from "@/components/wallpaper/DemoSwitcher";

export default function Page() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 p-6 bg-slate-100">
      <IpadFrame>
        <WallpaperStage />
        {/* Chat overlay on top of wallpaper — renders in both wallpaper & white mode */}
        <ChatOverlay />
      </IpadFrame>
      <DemoSwitcher />
    </main>
  );
}
