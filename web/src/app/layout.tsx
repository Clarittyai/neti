import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

// One family, everywhere — same choice as clarity-platform. Headings differ from body by
// weight and tracking, not by typeface.
const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "neti — a preflight gate for agent tool calls",
  description:
    "Before an agent acts, resolve what the action will actually touch and block it if it is bigger than you said it should be.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`}>{children}</body>
    </html>
  );
}
