import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, JetBrains_Mono } from "next/font/google";

import { Chrome } from "@/components/Chrome";

import "./globals.css";

/** The document voice: contemporary, slightly irregular, not a default grotesque. */
const bricolage = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-bricolage",
  display: "swap",
});

/** The machine voice. Every figure on this surface is tabular. */
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AlphaBrief — governed research agents",
  description:
    "Supervisor-pattern multi-agent research orchestration with MCP tooling, deterministic numeric verification and human-in-the-loop governance.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#0a0c10",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${bricolage.variable} ${jetbrains.variable}`}>
      <body className="antialiased">
        <Chrome>{children}</Chrome>
      </body>
    </html>
  );
}
