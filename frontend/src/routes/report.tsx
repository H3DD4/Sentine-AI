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
import { listFindings, generateReport } from "@/lib/api";
import type { Finding } from "@/lib/api";
import { ListSkeleton } from "@/components/ui/loading-skeletons";
import { toast } from "sonner";
import { FileDown, FileText, CheckCircle2, Layers, Loader2, CheckCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/report")({ component: ReportPage });

function ReportPage() {
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
        engagement_title: engagementTitle || "Penetration Test Report",
        client_name: clientName || "Client",
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

  const findingsCount = findings.length;
  const sections = [
    { name: "Executive Summary", count: "auto" },
    { name: "Scope & Methodology", count: "auto" },
    { name: "Findings Overview", count: `${findingsCount} findings` },
    { name: "Detailed Findings", count: `${selectedFindingIds.length} selected` },
    { name: "MITRE ATT&CK Mapping", count: "auto" },
    { name: "Remediation Roadmap", count: "auto" },
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
                className="font-mono"
              />
            </Field>
          </div>

          {/* Finding selector */}
          <div>
            <Label className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
              Select findings to include ({selectedFindingIds.length} of {findingsCount})
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
              {findings.map((f) => (
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
                  <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                    {f.verdict || "pending"}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <Label className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
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
              disabled={reportMutation.isPending || selectedFindingIds.length === 0}
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
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
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
                  <div className="flex h-6 w-6 items-center justify-center bg-brand-cyan-soft text-xs font-mono font-semibold text-brand-navy">
                    {String(i + 1).padStart(2, "0")}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium">{s.name}</div>
                    <div className="text-[11px] font-mono text-muted-foreground">{s.count}</div>
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
      <Label className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}
