/**
 * Source attribution UI.
 *
 * The product requirement these serve: the analyst must always be able to see
 * which knowledge sources an answer came from, and must be told plainly when
 * one was unavailable rather than silently receiving thinner coverage.
 *
 * Two distinct things are rendered here, and conflating them would mislead:
 *
 *   SourceBadge   — a source's *operational* state (from GET /kb/sources).
 *                   "Is the Ghostwriter corpus indexed and reachable?"
 *   ProvenanceBar — a source's contribution to *one specific answer*.
 *                   "Did Ghostwriter actually back what you just read?"
 *
 * A source can be perfectly healthy and still contribute nothing to a given
 * answer, so the second is never inferred from the first.
 */

import { cn } from "@/lib/utils";
import type { SourceHealth, SourceReport, SourceStatus } from "@/lib/api";
import { CheckCircle2, AlertTriangle, XCircle, CircleSlash, Database, Loader2 } from "lucide-react";

type StatusStyle = {
  label: string;
  className: string;
  Icon: typeof CheckCircle2;
};

/**
 * Colour carries meaning here: only a genuine fault is red. A source that is
 * simply empty or switched off is neutral grey — if every non-contributing
 * source looked like an error, a real outage would be invisible in the noise.
 *
 * These are design tokens, not raw palette shades. The previous
 * `emerald-600 / amber-600 / red-600 / slate-500` were fixed values picked
 * against white, and they stayed fixed in dark mode: mid-slate on the dark
 * navy sidebar was barely legible, so "Not indexed" and "Off" read as blank
 * space. The verdict/severity tokens already carry a lighter dark-mode value
 * (see `.dark` in styles.css), so the same semantic keeps its contrast in both
 * themes and the red/neutral distinction above survives the theme switch.
 */
const statusMap: Record<SourceStatus, StatusStyle> = {
  ok: {
    label: "Live",
    className: "text-verdict-confirmed bg-verdict-confirmed/10 ring-verdict-confirmed/30",
    Icon: CheckCircle2,
  },
  no_match: {
    label: "No match",
    className: "text-muted-foreground bg-muted-foreground/10 ring-muted-foreground/25",
    Icon: CircleSlash,
  },
  degraded: {
    label: "Partial",
    className: "text-verdict-review bg-verdict-review/10 ring-verdict-review/30",
    Icon: AlertTriangle,
  },
  empty: {
    label: "Not indexed",
    className: "text-muted-foreground bg-muted-foreground/10 ring-muted-foreground/25",
    Icon: Database,
  },
  unavailable: {
    label: "Unavailable",
    className: "text-verdict-false bg-verdict-false/10 ring-verdict-false/30",
    Icon: XCircle,
  },
  disabled: {
    // Dimmer than the other neutrals — switched off is the quietest state
    // there is, but it is still legible in both themes.
    label: "Off",
    className: "text-muted-foreground/70 bg-muted-foreground/5 ring-muted-foreground/20",
    Icon: CircleSlash,
  },
};

function styleFor(status: SourceStatus): StatusStyle {
  return statusMap[status] ?? statusMap.unavailable;
}

/** One source's operational state. `detail` is backend-authored, safe to show. */
export function SourceBadge({
  source,
  showCount = true,
  className,
}: {
  source: SourceHealth;
  showCount?: boolean;
  className?: string;
}) {
  const s = styleFor(source.status);
  return (
    <span
      title={source.detail}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        s.className,
        className,
      )}
    >
      <s.Icon className="h-3 w-3" strokeWidth={2.5} />
      <span className="font-semibold">{source.label}</span>
      {showCount && source.vector_count > 0 && (
        <span className="opacity-70 tabular-nums">{source.vector_count.toLocaleString()}</span>
      )}
      {source.status !== "ok" && <span className="opacity-70">· {s.label}</span>}
    </span>
  );
}

/** The full source panel — one badge per configured source. */
export function SourceHealthPanel({
  sources,
  loading,
  className,
}: {
  sources: SourceHealth[];
  loading?: boolean;
  className?: string;
}) {
  if (loading) {
    return (
      <div className={cn("flex items-center gap-2 text-xs text-muted-foreground", className)}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Checking knowledge sources…
      </div>
    );
  }

  const usable = sources.filter((s) => s.status === "ok" || s.status === "degraded");

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        {sources.map((s) => (
          <SourceBadge key={s.source} source={s} />
        ))}
      </div>
      {usable.length === 0 && sources.length > 0 && (
        // The one state the analyst must never miss: answers are still being
        // produced, but nothing is grounding them.
        <p className="flex items-start gap-1.5 text-[11px] text-verdict-false">
          <XCircle className="mt-px h-3 w-3 shrink-0" strokeWidth={2.5} />
          No knowledge source is available. Answers will not be grounded in the knowledge base —
          treat them as unverified.
        </p>
      )}
    </div>
  );
}

/**
 * Which sources backed one particular answer.
 *
 * Rendered under the message it describes, so the claim and the evidence for
 * that claim cannot be read apart.
 */
export function ProvenanceBar({
  reports,
  provenance,
  degraded,
  className,
}: {
  reports: SourceReport[];
  provenance?: string;
  degraded?: boolean;
  className?: string;
}) {
  if (!reports || reports.length === 0) return null;

  const contributed = reports.filter((r) => r.hits > 0);
  const failed = reports.filter((r) => r.status === "unavailable" || r.status === "degraded");

  return (
    <div className={cn("mt-2 space-y-1.5 border-t border-border/60 pt-2", className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-medium text-muted-foreground">Sources used:</span>
        {contributed.length === 0 ? (
          <span className="text-[11px] italic text-muted-foreground">
            none — not grounded in the knowledge base
          </span>
        ) : (
          contributed.map((r) => {
            const s = styleFor(r.status);
            return (
              <span
                key={r.source}
                title={r.detail || `${r.hits} result(s) in ${r.latency_ms}ms`}
                className={cn(
                  "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] ring-1 ring-inset",
                  s.className,
                )}
              >
                <span className="font-semibold">{r.label}</span>
                <span className="opacity-70 tabular-nums">{r.hits}</span>
              </span>
            );
          })
        )}
      </div>

      {degraded && failed.length > 0 && (
        <p className="flex items-start gap-1.5 text-[11px] text-verdict-review">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" strokeWidth={2.5} />
          <span>
            {failed.map((r) => `${r.label} — ${r.detail || r.status}`).join("; ")}. This answer is
            based only on the sources listed above.
          </span>
        </p>
      )}

      {provenance && (
        // The backend's own sentence, kept available verbatim: it is what gets
        // written into the audit log, so the UI should not paraphrase it.
        <p className="sr-only">{provenance}</p>
      )}
    </div>
  );
}
