"use client";

import { useState } from "react";

import type { GatePayload } from "@/lib/types";

type Action = "approve" | "reject" | "edit";

/**
 * The human gate. The graph is suspended at a checkpointed LangGraph interrupt;
 * these three keys are the only thing that resumes it.
 */
export function ApprovalGate({
  gate,
  onDecided,
}: {
  gate: GatePayload;
  onDecided: (status: string) => void;
}) {
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [headline, setHeadline] = useState(gate.brief.headline);
  const [note, setNote] = useState("");

  const decide = async (action: Action) => {
    setBusy(action);
    setError(null);
    try {
      const response = await fetch(`/api/decision/${gate.run_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          reviewer: "analyst",
          note: note || undefined,
          edited_headline: action === "edit" ? headline : undefined,
        }),
      });
      const payload = (await response.json()) as { status?: string; detail?: string };
      if (!response.ok) {
        setError(payload.detail ?? "The decision could not be recorded.");
        return;
      }
      onDecided(payload.status ?? "DELIVERED");
    } catch {
      setError("Network error while recording the decision.");
    } finally {
      setBusy(null);
    }
  };

  const verified = gate.verified;

  return (
    <section
      className={`panel ticked border-2 ${verified ? "border-phosphor-dim" : "border-alert-dim"}`}
    >
      <header
        className={`flex flex-wrap items-center gap-x-4 gap-y-2 rule-b px-3 py-2 ${
          verified ? "bg-phosphor/6" : "bg-alert/8"
        }`}
      >
        <span className="flex-1 text-[11px] uppercase tracking-[0.2em]">
          <span className={verified ? "text-phosphor" : "text-alert"}>
            {verified ? "◆ execution halted — sign-off required" : "▲ verification failed — review"}
          </span>
        </span>
        <span
          className={`border px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] ${
            verified
              ? "border-phosphor-dim text-phosphor"
              : "border-alert-dim text-alert"
          }`}
        >
          {gate.verification.matched}/{gate.verification.checked_claims} verified
        </span>
      </header>

      <div className="space-y-3 px-3 py-3">
        <p className="text-[11.5px] leading-relaxed text-muted">
          {verified
            ? "Every figure reconciled against raw market data. The graph is paused at a checkpointed interrupt and cannot deliver without a decision."
            : "One or more figures did not reconcile. The brief was regenerated once and still failed, so it is held here rather than delivered."}
        </p>

        {editing ? (
          <label className="block">
            <span className="label">headline</span>
            <textarea
              value={headline}
              onChange={(event) => setHeadline(event.target.value)}
              rows={2}
              className="mt-1 w-full resize-none border border-rule bg-black px-2.5 py-2 text-[12px] text-ink outline-none focus:border-amber-dim"
            />
            <span className="mt-1 block text-[10px] text-faint">
              narrative only — a numeral typed here is rejected; figures stay verified claims
            </span>
          </label>
        ) : null}

        <label className="block">
          <span className="label">reviewer note (optional)</span>
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={1000}
            placeholder="recorded in the approval audit trail"
            className="mt-1 w-full border border-rule bg-black px-2.5 py-2 text-[12px] text-ink outline-none placeholder:text-faint focus:border-amber-dim"
          />
        </label>

        {error ? (
          <p className="border border-alert-dim bg-alert/10 px-2.5 py-1.5 text-[11px] text-alert">
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Key
            tone="phosphor"
            busy={busy === "approve"}
            disabled={busy !== null}
            onClick={() => decide("approve")}
            label="approve & deliver"
          />
          {editing ? (
            <Key
              tone="amber"
              busy={busy === "edit"}
              disabled={busy !== null}
              onClick={() => decide("edit")}
              label="save edit & deliver"
            />
          ) : (
            <Key
              tone="neutral"
              disabled={busy !== null}
              onClick={() => setEditing(true)}
              label="edit headline"
            />
          )}
          <Key
            tone="alert"
            busy={busy === "reject"}
            disabled={busy !== null}
            onClick={() => decide("reject")}
            label="reject"
          />
        </div>
      </div>
    </section>
  );
}

function Key({
  label,
  tone,
  onClick,
  disabled,
  busy,
}: {
  label: string;
  tone: "phosphor" | "amber" | "alert" | "neutral";
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  const tones = {
    phosphor: "border-phosphor-dim text-phosphor hover:bg-phosphor/12",
    amber: "border-amber-dim text-amber hover:bg-amber/12",
    alert: "border-alert-dim text-alert hover:bg-alert/12",
    neutral: "border-rule text-muted hover:border-rule-hot hover:text-ink",
  } as const;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`border px-3.5 py-2 text-[11px] uppercase tracking-[0.16em] transition disabled:opacity-40 ${tones[tone]}`}
    >
      {busy ? "…" : label}
    </button>
  );
}
