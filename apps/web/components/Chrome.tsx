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
  durable_checkpoints?: boolean;
  models?: Record<string, string>;
}

/**
 * The chassis: identity, navigation, and an honest readout of how this process
 * is actually configured — including whether a paused run would survive a
 * restart, which is a property of the running system rather than of the config
 * file, and therefore worth showing.
 */
export function Chrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [health, setHealth] = useState<Health | null>(null);

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

  const online = health?.status === "ok";
  const onClaude = health?.engine === "anthropic";

  return (
    <div className="field relative min-h-dvh">
      <header className="sticky top-0 z-50 border-b border-edge bg-void/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-[1560px] flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3">
          <Link href="/" className="flex items-baseline gap-2.5">
            <span className="display text-[17px] font-extrabold tracking-[-0.03em] text-ink">
              Alpha<span className="text-violet">brief</span>
            </span>
            <span className="hidden text-[9px] uppercase tracking-[0.22em] text-faint sm:inline">
              orchestration instrument
            </span>
          </Link>

          <nav className="flex items-center gap-1 text-[10px] uppercase tracking-[0.16em]">
            {[
              { href: "/", label: "Console" },
              { href: "/archive", label: "Archive" },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`btn px-3 py-1.5 ${
                  pathname === item.href
                    ? "border-violet-dim bg-violet-wash !text-violet"
                    : "hover:border-edge-hot hover:!text-ink"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 text-[10px] uppercase tracking-[0.14em]">
            <Field label="api">
              <span className={online ? "text-violet" : "text-coral"}>
                <span
                  className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                    online ? "bg-violet blink" : "bg-coral"
                  }`}
                  aria-hidden
                />
                {online ? "connected" : "unreachable"}
              </span>
            </Field>

            {health?.engine ? (
              <Field label="reasoning">
                <span className={onClaude ? "text-live" : "text-muted"}>
                  {onClaude ? shortModel(health.models?.worker ?? "claude") : "deterministic"}
                </span>
              </Field>
            ) : null}

            {health?.durable_checkpoints !== undefined ? (
              <Field label="gate">
                <span className={health.durable_checkpoints ? "text-violet" : "text-coral"}>
                  {health.durable_checkpoints ? "survives restart" : "in-memory"}
                </span>
              </Field>
            ) : null}
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full max-w-[1560px] px-5 py-6">{children}</main>

      <footer className="relative z-10 mx-auto w-full max-w-[1560px] border-t border-edge px-5 py-5">
        <p className="max-w-3xl text-[11px] leading-relaxed text-faint">
          The model never does arithmetic. Tools compute every figure over MCP, a deterministic node
          recomputes each one from the raw bars using a different implementation, and a human signs
          before anything ships. Hallucinated numbers are impossible by construction rather than by
          asking nicely in a prompt.
        </p>
      </footer>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="whitespace-nowrap">
      <span className="text-faint">{label}</span>
      <span className="mx-1.5 text-edge-hot">/</span>
      {children}
    </span>
  );
}

function shortModel(model: string): string {
  return model.replace("claude-", "").replace(/-\d{8}$/, "");
}
