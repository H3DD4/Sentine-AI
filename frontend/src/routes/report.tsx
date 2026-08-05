import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { generateReport, listFindings, REPORT_HANDOFF_STORAGE_KEY } from "@/lib/api";
import type { ConversationReportHandoff, Finding } from "@/lib/api";
import { ListSkeleton } from "@/components/ui/loading-skeletons";
import { toast } from "sonner";
import { AlertCircle, CheckCircle, CheckCircle2, FileDown, FileText, Layers } from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/report")({ component: ReportPage });

function loadConversationHandoff(): ConversationReportHandoff | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(REPORT_HANDOFF_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as ConversationReportHandoff) : null;
  } catch {
    return null;
  }
}

function ReportPage() {
  const [conversationHandoff] = useState(loadConversationHandoff);
  const [clientName, setClientName] = useState("");
  const [engagementTitle, setEngagementTitle] = useState("");
  // DOCX only, because that is the one format the backend actually produces
  // (`/reports/generate` returns a Word document unconditionally). The page
  // previously offered a PDF toggle that defaulted to on, so the default
  // download was a Word file named `.pdf` — it failed to open, and the cause
  // was invisible. Restore the toggle when a PDF renderer exists server-side.
  const format = "docx" as const;
  const [selectedFindingIds, setSelectedFindingIds] = useState<string[]>([]);

  // Fetch all findings for selection
  const { data: findings = [], isLoading: findingsLoading } = useQuery<Finding[]>({
    queryKey: ["findings"],
    queryFn: () => listFindings(0),
  });

  // Report generation mutation
  const reportMutation = useMutation({
    mutationFn: async () => {
      const blob = await generateReport({
        finding_ids: selectedFindingIds,
        engagement_title: engagementTitle.trim(),
        client_name: clientName.trim(),
        draft: conversationHandoff?.readiness.draft,
      });
      // Trigger download
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report-${engagementTitle.replace(/\s+/g, "-").toLowerCase() || "engagement"}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
    onSuccess: () => {
      try {
        sessionStorage.removeItem(REPORT_HANDOFF_STORAGE_KEY);
      } catch {
        // The download succeeded; unavailable browser storage is non-fatal.
      }
      toast.success("Report generated", {
        description: "Your report has been downloaded successfully.",
      });
    },
    onError: (error) => {
      toast.error("Report generation failed", {
        description:
          error instanceof Error
            ? error.message
            : "Please check the backend is running and try again.",
      });
    },
  });

  const toggleFinding = (id: string) => {
    setSelectedFindingIds((prev) =>
      prev.includes(id) ? prev.filter((fid) => fid !== id) : [...prev, id],
    );
  };

  const availableFindings = findings.filter(
    (finding) => finding.id !== conversationHandoff?.finding_id,
  );
  const findingsCount = availableFindings.length + (conversationHandoff ? 1 : 0);
  const reportItemCount = selectedFindingIds.length + (conversationHandoff ? 1 : 0);
  const sections = [
    { name: "Executive Summary", count: "auto" },
    { name: "Scope & Methodology", count: "auto" },
    { name: "Findings Overview", count: `${findingsCount} findings` },
    { name: "Detailed Findings", count: `${reportItemCount} selected` },
    { name: "MITRE ATT&CK Mapping", count: "auto" },
    { name: "Evidence Completeness", count: "auto" },
    { name: "Appendices & Evidence", count: "auto" },
  ];

  return (
    <AppShell>
      <PageHeader
        eyebrow="Report Builder"
        title="Generate engagement report"
        description="Fill the metadata, select findings to include, then export a client-ready deliverable."
      />

      <div className="grid grid-cols-1 gap-6 px-5 py-7 md:px-8 lg:px-10 xl:grid-cols-[minmax(0,1fr)_440px]">
        {/* Form */}
        <Card className="space-y-6 border-border bg-white p-6 shadow-soft">
          {conversationHandoff && (
            <div className="border border-border bg-[#fafafa] p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold text-brand-navy">
                    <CheckCircle2 className="h-4 w-4 text-brand-cyan" />
                    Conversation draft included
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {conversationHandoff.readiness.summary}
                  </p>
                </div>
                <div className="text-lg font-semibold tabular-nums text-brand-navy">
                  {conversationHandoff.readiness.score.toFixed(1)} / 10
                </div>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden bg-muted">
                <div
                  className="h-full bg-brand-cyan"
                  style={{ width: `${conversationHandoff.readiness.score * 10}%` }}
                />
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <DraftField label="Finding" value={conversationHandoff.readiness.draft.title} />
                <DraftField
                  label="Affected scope"
                  value={conversationHandoff.readiness.draft.affected_scope}
                />
                <DraftField
                  label="Technical evidence"
                  value={conversationHandoff.readiness.draft.technical_evidence}
                />
                <DraftField label="Impact" value={conversationHandoff.readiness.draft.impact} />
                <DraftField
                  label="Severity / CVSS"
                  value={[
                    conversationHandoff.readiness.draft.severity,
                    conversationHandoff.readiness.draft.cvss_score?.toFixed(1),
                    conversationHandoff.readiness.draft.cvss_vector,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
              </div>
              {conversationHandoff.readiness.missing.length > 0 && (
                <div className="mt-4 flex items-start gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sev-medium" />
                  <span>
                    <strong className="text-foreground">Still incomplete:</strong>{" "}
                    {conversationHandoff.readiness.missing.join(", ")}. You can generate now or
                    return to Analysis to strengthen these sections.
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-brand-cyan" />
            <h2 className="text-lg font-semibold">Engagement details</h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Client / Target">
              <Input
                value={clientName}
                onChange={(e) => setClientName(e.target.value)}
                placeholder="e.g. Northwind Bank"
              />
            </Field>
            <Field label="Engagement title">
              <Input
                value={engagementTitle}
                onChange={(e) => setEngagementTitle(e.target.value)}
                placeholder="e.g. External PT Q1 2026"
              />
            </Field>
          </div>

          {/* Finding selector */}
          <div>
            <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Add saved findings ({selectedFindingIds.length} selected)
            </Label>
            <div className="mt-2 max-h-52 space-y-1 overflow-y-auto border border-border p-2">
              {findingsLoading && (
                <div className="py-2">
                  <ListSkeleton count={3} />
                </div>
              )}
              {!findingsLoading && findings.length === 0 && (
                <div className="text-center py-4 text-sm text-muted-foreground">
                  No findings yet. Validate one on the{" "}
                  <Link to="/chat" className="text-brand-cyan underline">
                    Analysis page
                  </Link>{" "}
                  and it will appear here.
                </div>
              )}
              {availableFindings.map((f) => (
                <button
                  key={f.id}
                  onClick={() => toggleFinding(f.id)}
                  className={cn(
                    "flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors",
                    selectedFindingIds.includes(f.id) ? "bg-brand-cyan-soft" : "hover:bg-muted",
                  )}
                >
                  <div
                    className={cn(
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border",
                      selectedFindingIds.includes(f.id)
                        ? "bg-brand-cyan border-brand-cyan"
                        : "border-border",
                    )}
                  >
                    {selectedFindingIds.includes(f.id) && (
                      <CheckCircle className="h-3 w-3 text-white" />
                    )}
                  </div>
                  <span className="flex-1 truncate">{f.title}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {f.verdict || "pending"}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Export format
            </Label>
            <div className="mt-2 flex items-center gap-2">
              <span className="bg-brand-navy px-4 py-2 text-sm font-semibold text-white">docx</span>
              <span className="text-xs text-muted-foreground">
                Word document — open and edit before sending to the client.
              </span>
            </div>
          </div>

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
            <Button
              className="min-w-40"
              onClick={() => reportMutation.mutate()}
              disabled={
                reportMutation.isPending ||
                reportItemCount === 0 ||
                !clientName.trim() ||
                !engagementTitle.trim()
              }
            >
              {reportMutation.isPending ? (
                <span className="h-4 w-4 mr-1.5 inline-block rounded-full border-2 border-white/30 border-t-white animate-spin" />
              ) : (
                <FileDown className="h-4 w-4 mr-1.5" />
              )}
              {reportMutation.isPending ? "Generating..." : `Generate ${format.toUpperCase()}`}
            </Button>
          </div>

          {reportMutation.isError && (
            <div className="rounded-lg bg-sev-critical/10 ring-1 ring-inset ring-sev-critical/30 p-3 text-sm text-sev-critical">
              Failed to generate report. Please check the backend is running and try again.
            </div>
          )}
        </Card>

        {/* Structure preview */}
        <Card className="border-border bg-[#f4f4f4] p-6 shadow-soft">
          <div className="flex items-center gap-2 mb-4">
            <Layers className="h-4 w-4 text-brand-cyan" />
            <h2 className="text-base font-semibold">Report structure</h2>
          </div>

          <div className="overflow-hidden border border-border bg-white shadow-soft">
            <div className="border-b border-border bg-white px-5 py-4">
              <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                Confidential · TLP:RED
              </div>
              <div className="text-lg font-semibold leading-tight">
                {clientName || "Client Name"}
              </div>
              <div className="text-xs text-muted-foreground">
                Penetration Test Report · {engagementTitle || "Engagement Title"}
              </div>
            </div>
            <ol className="divide-y divide-border">
              {sections.map((s, i) => (
                <li key={s.name} className="flex items-center gap-3 px-4 py-3">
                  <div className="flex h-6 w-6 items-center justify-center bg-brand-cyan-soft text-xs font-semibold tabular-nums text-brand-navy">
                    {String(i + 1).padStart(2, "0")}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium">{s.name}</div>
                    <div className="text-[11px] tabular-nums text-muted-foreground">{s.count}</div>
                  </div>
                  <CheckCircle2 className="h-4 w-4 text-verdict-confirmed" />
                </li>
              ))}
            </ol>
          </div>

          <div className="mt-4 rounded-lg bg-brand-cyan/10 ring-1 ring-inset ring-brand-cyan/30 p-3 text-xs text-foreground">
            <span className="font-semibold">AI compose:</span> executive summary, remediation
            timeline, and MITRE mapping will be auto-generated from validated findings.
          </div>
        </Card>
      </div>
    </AppShell>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function DraftField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 line-clamp-3 whitespace-pre-wrap text-sm leading-relaxed">
        {value || "Not provided"}
      </div>
    </div>
  );
}
