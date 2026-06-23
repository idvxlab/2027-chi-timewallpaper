import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Time Wallpaper",
  description: "陪伴型动态时间壁纸",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
