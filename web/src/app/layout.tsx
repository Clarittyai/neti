import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { ConsoleProvider } from "@/components/ConsoleProvider";
import { Shell } from "@/components/Shell";
import "./globals.css";

// One family, everywhere — the same choice as clarity-platform. Headings differ from body by
// weight and tracking, not by typeface.
const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "neti — preflight gate",
  description:
    "Before an agent acts, resolve what the action will actually touch and stop it if it is bigger than you said it should be.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Dark by default, and not as a style preference: on the light surface the reserved emerald and
  // amber both fall under 3:1 against the page. This is a status surface, and dark is where its
  // palette actually works. Light stays fully tokenised for anyone who wants it.
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className={`${inter.variable} bg-background font-sans text-foreground antialiased`}>
        <ConsoleProvider>
          <Shell>{children}</Shell>
        </ConsoleProvider>
      </body>
    </html>
  );
}
