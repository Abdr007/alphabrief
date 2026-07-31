"use client";

import { useState } from "react";

import type { GatePayload } from "@/lib/types";

type Action = "approve" | "reject" | "edit";

/**
 * The seam between the machine and the document.
 *
 * The graph is genuinely suspended here, at a checkpointed LangGraph interrupt
 * — not waiting on a callback held in memory. That is why the decision is a
 * separate authenticated request, and why it still works after a restart.
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
      className={`panel-flush overflow-hidden border-2 ${
        verified ? "border-violet-dim" : "border-coral-dim"
      }`}
    >
      <header
        className={`flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-3 ${
          verified ? "border-violet-dim bg-violet-wash" : "border-coral-dim bg-coral/8"
        }`}
      >
        <div className="flex-1">
          <p
            className={`display text-[15px] font-bold tracking-[-0.02em] ${
              verified ? "text-violet" : "text-coral"
            }`}
          >
            {verified ? "Paused for your signature" : "Held back — a figure did not reconcile"}
          </p>
        </div>
        <span
          className={`rounded-[2px] border px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] ${
            verified ? "border-violet-dim text-violet" : "border-coral-dim text-coral"
          }`}
        >
          {gate.verification.matched}/{gate.verification.checked_claims} agreed
        </span>
      </header>

      <div className="space-y-4 px-4 py-4">
        <p className="max-w-3xl text-[11.5px] leading-relaxed text-muted">
          {verified
            ? "Every figure was recomputed from the raw bars and matched. Nothing is delivered until you decide."
            : "A figure survived neither the first write nor the one permitted regeneration, so the brief is held here instead of being sent."}
        </p>

        {editing ? (
          <label className="block">
            <span className="eyebrow">Headline</span>
            <textarea
              value={headline}
              onChange={(event) => setHeadline(event.target.value)}
              rows={2}
              className="mt-1.5 w-full resize-none rounded-[2px] border border-edge bg-void px-3 py-2 text-[12px] text-ink outline-none focus:border-violet-dim"
            />
            <span className="mt-1 block text-[10px] text-faint">
              Narrative only. A numeral typed here is rejected — figures stay verified claims.
            </span>
          </label>
        ) : null}

        <label className="block">
          <span className="eyebrow">Note for the audit trail</span>
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={1000}
            placeholder="Optional — stored with the decision"
            className="mt-1.5 w-full rounded-[2px] border border-edge bg-void px-3 py-2 text-[12px] text-ink outline-none placeholder:text-faint focus:border-violet-dim"
          />
        </label>

        {error ? (
          <p className="rounded-[2px] border border-coral-dim bg-coral/10 px-3 py-2 text-[11px] text-coral">
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Key
            tone="primary"
            busy={busy === "approve"}
            disabled={busy !== null}
            onClick={() => decide("approve")}
            label="Approve and send"
          />
          {editing ? (
            <Key
              tone="neutral"
              busy={busy === "edit"}
              disabled={busy !== null}
              onClick={() => decide("edit")}
              label="Save edit and send"
            />
          ) : (
            <Key
              tone="neutral"
              disabled={busy !== null}
              onClick={() => setEditing(true)}
              label="Edit headline"
            />
          )}
          <Key
            tone="danger"
            busy={busy === "reject"}
            disabled={busy !== null}
            onClick={() => decide("reject")}
            label="Reject"
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
  tone: "primary" | "danger" | "neutral";
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  const tones = {
    primary: "border-violet !text-live bg-violet/20 hover:bg-violet/32",
    danger: "border-coral-dim !text-coral hover:bg-coral/12",
    neutral: "hover:border-edge-hot hover:!text-ink",
  } as const;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`btn px-4 py-2.5 disabled:opacity-40 ${tones[tone]}`}
    >
      {busy ? "Working…" : label}
    </button>
  );
}
