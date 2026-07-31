"use client";

import { Fragment, type ReactNode } from "react";

import { formatClaim } from "@/lib/format";
import type { NumericClaim } from "@/lib/types";

/**
 * Renders brief prose with every `{{cN}}` reference as a citation chip.
 *
 * The brief schema rejects any narrative containing a bare numeral, so a figure
 * can only reach the page as a reference into the claim table. Substituting it
 * into plain text would hide that; a chip makes it visible — and hovering one
 * lights up the matching track in the convergence lattice, so a sentence and
 * the proof behind it are one gesture apart.
 */

const CLAIM_REF = /\{\{(c\d+)\}\}/g;

export function renderWithCitations(
  text: string,
  claims: Map<string, NumericClaim>,
  activeClaim: string | null,
  onActiveClaim: (claimId: string | null) => void,
): ReactNode {
  const parts: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  // A fresh regex per call: /g carries lastIndex across invocations.
  const pattern = new RegExp(CLAIM_REF.source, "g");

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push(<Fragment key={`t${cursor}`}>{text.slice(cursor, match.index)}</Fragment>);
    }
    // The capture group is not optional in the pattern, but the compiler cannot
    // know that; skipping is the honest fallback for an impossible match.
    const id = match[1];
    if (id === undefined) {
      cursor = match.index + match[0].length;
      continue;
    }
    parts.push(
      <Citation
        key={`c${match.index}`}
        claimId={id}
        claim={claims.get(id)}
        active={activeClaim === id}
        onActiveClaim={onActiveClaim}
      />,
    );
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    parts.push(<Fragment key={`t${cursor}`}>{text.slice(cursor)}</Fragment>);
  }
  return parts;
}

export function Citation({
  claimId,
  claim,
  active,
  onActiveClaim,
}: {
  claimId: string;
  claim: NumericClaim | undefined;
  active: boolean;
  onActiveClaim: (claimId: string | null) => void;
}) {
  const label = claim
    ? `${claimId} · ${claim.ticker} ${claim.metric} — tool-computed, then independently recomputed`
    : `${claimId} — no matching claim in the table`;

  return (
    <span
      className={`cite ${claim ? "" : "cite-unverified"}`}
      data-active={active ? "true" : undefined}
      title={label}
      tabIndex={0}
      role="button"
      aria-label={label}
      onMouseEnter={() => onActiveClaim(claimId)}
      onMouseLeave={() => onActiveClaim(null)}
      onFocus={() => onActiveClaim(claimId)}
      onBlur={() => onActiveClaim(null)}
    >
      {formatClaim(claim)}
    </span>
  );
}
