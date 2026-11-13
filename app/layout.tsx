import type { Metadata, Viewport } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "Nexora v6 — ফাইল ডাউনলোড",
  description: "Nexora Telegram Bot v6-এর সম্পূর্ণ ফোল্ডার একটি ZIP archive হিসেবে ডাউনলোড করুন।",
  robots: { index: false, follow: false },
}

export const viewport: Viewport = {
  themeColor: "#0c0d12",
  width: "device-width",
  initialScale: 1,
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="bn">
      <body>{children}</body>
    </html>
  )
}
