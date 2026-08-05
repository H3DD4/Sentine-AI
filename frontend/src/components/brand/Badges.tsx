import { cn } from "@/lib/utils";
import type { Severity, Verdict } from "@/lib/api";
import { CheckCircle2, AlertTriangle, XCircle, HelpCircle } from "lucide-react";

const sevMap: Record<Severity, { label: string; color: string; ring: string }> = {
  critical: {
    label: "Critical",
    color: "text-sev-critical",
    ring: "ring-sev-critical/40 bg-sev-critical/12",
  },
  high: { label: "High", color: "text-sev-high", ring: "ring-sev-high/40 bg-sev-high/12" },
  medium: {
    label: "Medium",
    color: "text-sev-medium",
    ring: "ring-sev-medium/40 bg-sev-medium/15",
  },
  low: { label: "Low", color: "text-sev-low", ring: "ring-sev-low/40 bg-sev-low/12" },
  info: { label: "Info", color: "text-sev-info", ring: "ring-sev-info/40 bg-sev-info/12" },
};

export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const s = sevMap[severity];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
        s.color,
        s.ring,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", `bg-sev-${severity}`)} />
      {s.label}
    </span>
  );
}

/**
 * Keyed on the backend's own verdict values. An earlier version had its own
 * three-value vocabulary (confirmed/review/false) that callers mapped onto,
 * which collapsed "likely" and "insufficient" into one badge — two verdicts an
 * analyst has to act on very differently.
 */
const verdictMap: Record<Verdict, { label: string; token: string; Icon: typeof CheckCircle2 }> = {
  confirmed: { label: "Confirmed", token: "verdict-confirmed", Icon: CheckCircle2 },
  likely: { label: "Likely", token: "verdict-confirmed", Icon: AlertTriangle },
  insufficient: { label: "Insufficient", token: "verdict-review", Icon: HelpCircle },
  false_positive: { label: "False Positive", token: "verdict-false", Icon: XCircle },
};

export function VerdictBadge({
  verdict,
  size = "md",
  className,
}: {
  verdict: Verdict;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const v = verdictMap[verdict];
  const sizes = {
    sm: "px-2.5 py-1 text-[11px] gap-1.5",
    md: "px-3 py-1.5 text-xs gap-2",
    lg: "px-4 py-2 text-sm gap-2.5",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm font-semibold text-white",
        sizes[size],
        className,
      )}
      style={{
        background: `var(--${v.token})`,
      }}
    >
      <v.Icon className={cn(size === "lg" ? "h-4 w-4" : "h-3.5 w-3.5")} strokeWidth={2.5} />
      {v.label}
    </span>
  );
}
