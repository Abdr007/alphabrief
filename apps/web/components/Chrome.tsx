"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

interface Health {
  status: string;
  version?: string;
  engine?: string;
  mcp_transport?: string;
  langfuse?: boolean;
  models?: Record<string, string>;
}

/** Terminal status bar: link state, model routing, session clock. */
export function Chrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [health, setHealth] = useState<Health | null>(null);
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health", { cache: "no-store" })
      .then((response) => response.json())
      .then((payload: Health) => {
        if (!cancelled) setHealth(payload);
      })
      .catch(() => {
        if (!cancelled) setHealth({ status: "unreachable" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().slice(11, 19));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);

  const online = health?.status === "ok";

  return (
    <div className="crt relative min-h-dvh">
      {/* ── command strip ─────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 border-b border-rule bg-shell/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-5 gap-y-2 px-5 py-2.5">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="text-[15px] leading-none text-amber glow-amber">▚</span>
            <span className="text-[13px] font-semibold uppercase tracking-[0.24em] text-ink">
              Alpha<span className="text-amber">brief</span>
            </span>
          </Link>

          <span className="hidden text-[10px] uppercase tracking-[0.16em] text-faint sm:inline">
            governed research terminal
          </span>

          <nav className="flex items-center gap-0.5 text-[11px] uppercase tracking-[0.14em]">
            {[
              { href: "/", label: "Console" },
              { href: "/archive", label: "Archive" },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`border px-2.5 py-1 transition ${
                  pathname === item.href
                    ? "border-amber-dim bg-amber/10 text-amber"
                    : "border-transparent text-faint hover:border-rule hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] uppercase tracking-[0.12em]">
            <Field label="link">
              <span className={online ? "text-phosphor" : "text-alert"}>
                <span
                  className={`mr-1.5 inline-block h-1.5 w-1.5 align-middle ${
                    online ? "bg-phosphor blink" : "bg-alert"
                  }`}
                />
                {online ? "online" : "offline"}
              </span>
            </Field>

            {health?.engine ? (
              <Field label="engine">
                <span className={health.engine === "anthropic" ? "text-amber" : "text-muted"}>
                  {health.engine}
                </span>
              </Field>
            ) : null}

            {health?.models?.supervisor ? (
              <Field label="route">
                <span className="text-signal">{shortModel(health.models.supervisor)}</span>
                <span className="mx-1 text-faint">▸</span>
                <span className="text-amber">{shortModel(health.models.worker ?? "")}</span>
              </Field>
            ) : null}

            {health?.mcp_transport ? (
              <Field label="mcp">
                <span className="text-muted">{health.mcp_transport}</span>
              </Field>
            ) : null}

            <Field label="utc">
              <span className="text-ink">{clock}</span>
            </Field>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full max-w-[1500px] px-5 py-6">{children}</main>

      <footer className="relative z-10 mx-auto w-full max-w-[1500px] border-t border-rule px-5 py-4">
        <p className="max-w-4xl text-[11px] leading-relaxed text-faint">
          <span className="text-amber-dim">◆</span> The LLM never does arithmetic. Tools compute
          over MCP, a deterministic node recomputes every figure in the final brief, and a human
          gate signs off — hallucinated numbers are impossible by construction, not by
          prompt-begging.
        </p>
      </footer>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="whitespace-nowrap">
      <span className="text-faint">{label}</span>
      <span className="mx-1 text-rule-hot">:</span>
      {children}
    </span>
  );
}

function shortModel(model: string): string {
  return model.replace("claude-", "").replace("#deterministic", "").replace(/-\d+$/, "");
}
