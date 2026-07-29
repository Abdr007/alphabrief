import type { Metadata, Viewport } from "next";

import { Chrome } from "@/components/Chrome";

import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaBrief — governed research agents",
  description:
    "Supervisor-pattern multi-agent research orchestration with MCP tooling, deterministic numeric verification and human-in-the-loop governance.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#08090b",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        <Chrome>{children}</Chrome>
      </body>
    </html>
  );
}
