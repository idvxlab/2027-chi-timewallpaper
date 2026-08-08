import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AppPreferencesHydrator } from "@/components/shared/AppPreferencesHydrator";

export const metadata: Metadata = {
  title: "Time Wallpaper",
  description: "陪伴型动态时间壁纸",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <AppPreferencesHydrator />
        {children}
      </body>
    </html>
  );
}
