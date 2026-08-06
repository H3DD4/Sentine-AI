import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createFinding,
  deleteFinding,
  deleteReport,
  downloadReport,
  generateReport,
  listFindings,
  listReports,
  REPORT_HANDOFF_STORAGE_KEY,
  updateFinding,
} from "@/lib/api";
import type { ConversationReportHandoff, Finding, GeneratedReport, Verdict } from "@/lib/api";
import { ListSkeleton } from "@/components/ui/loading-skeletons";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle,
  CheckCircle2,
  Download,
  FileDown,
  FileText,
  Layers,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
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

function suggestedTarget(handoff: ConversationReportHandoff | null): string {
  if (!handoff) return "";
  const source = [
    handoff.readiness.draft.affected_scope,
    handoff.readiness.draft.title,
    ...handoff.messages.map((message) => message.content),
  ].join(" ");
  return source.match(/\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b/i)?.[0] ?? "";
}

function ReportPage() {
  const queryClient = useQueryClient();
  const [conversationHandoff, setConversationHandoff] = useState<ConversationReportHandoff | null>(
    null,
  );
  const [clientName, setClientName] = useState("");
  const [engagementTitle, setEngagementTitle] = useState("");
  // DOCX only, because that is the one format the backend actually produces
  // (`/reports/generate` returns a Word document unconditionally). The page
  // previously offered a PDF toggle that defaulted to on, so the default
  // download was a Word file named `.pdf` — it failed to open, and the cause
  // was invisible. Restore the toggle when a PDF renderer exists server-side.
  const format = "docx" as const;
  const [selectedFindingIds, setSelectedFindingIds] = useState<string[]>([]);
  const [includeConversationDraft, setIncludeConversationDraft] = useState(false);
  const [generationAttempted, setGenerationAttempted] = useState(false);
  const [findingSearch, setFindingSearch] = useState("");
  const [editingFinding, setEditingFinding] = useState<Finding | null | undefined>(undefined);
  const [findingTitle, setFindingTitle] = useState("");
  const [findingDescription, setFindingDescription] = useState("");
  const [findingVerdict, setFindingVerdict] = useState<Verdict | "pending">("pending");

  useEffect(() => {
    const handoff = loadConversationHandoff();
    if (!handoff) return;
    const target = suggestedTarget(handoff);
    setConversationHandoff(handoff);
    setClientName((current) => current || target);
    setEngagementTitle((current) => current || (target ? `Penetration Test - ${target}` : ""));
    setIncludeConversationDraft(true);
  }, []);

  // Fetch all findings for selection
  const { data: findings = [], isLoading: findingsLoading } = useQuery<Finding[]>({
    queryKey: ["findings"],
    queryFn: () => listFindings(0, 200),
  });

  const { data: reports = [], isLoading: reportsLoading } = useQuery<GeneratedReport[]>({
    queryKey: ["reports"],
    queryFn: listReports,
  });

  // Report generation mutation
  const reportMutation = useMutation({
    mutationFn: async () => {
      const blob = await generateReport({
        finding_ids: selectedFindingIds,
        engagement_title: engagementTitle.trim(),
        client_name: clientName.trim(),
        draft: includeConversationDraft ? conversationHandoff?.readiness.draft : undefined,
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
      queryClient.invalidateQueries({ queryKey: ["reports"] });
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

  const findingMutation = useMutation({
    mutationFn: () => {
      const title = findingTitle.trim();
      const description = findingDescription.trim();
      if (editingFinding) {
        return updateFinding(editingFinding.id, {
          title,
          description,
          verdict: findingVerdict === "pending" ? null : findingVerdict,
        });
      }
      return createFinding(title, description);
    },
    onSuccess: async (finding) => {
      if (!editingFinding && findingVerdict !== "pending") {
        await updateFinding(finding.id, { verdict: findingVerdict });
      }
      queryClient.invalidateQueries({ queryKey: ["findings"] });
      setEditingFinding(undefined);
      toast.success(editingFinding ? "Finding updated" : "Finding added");
    },
    onError: (error) => toast.error("Could not save finding", { description: error.message }),
  });

  const removeFindingMutation = useMutation({
    mutationFn: deleteFinding,
    onSuccess: (_, id) => {
      setSelectedFindingIds((current) => current.filter((findingId) => findingId !== id));
      queryClient.invalidateQueries({ queryKey: ["findings"] });
      toast.success("Finding deleted");
    },
    onError: (error) => toast.error("Could not delete finding", { description: error.message }),
  });

  const removeReportMutation = useMutation({
    mutationFn: deleteReport,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reports"] });
      toast.success("Report deleted");
    },
    onError: (error) => toast.error("Could not delete report", { description: error.message }),
  });

  const openFindingEditor = (finding: Finding | null) => {
    setEditingFinding(finding);
    setFindingTitle(finding?.title ?? "");
    setFindingDescription(finding?.description ?? "");
    setFindingVerdict(finding?.verdict ?? "pending");
  };

  const downloadHistoricalReport = async (report: GeneratedReport) => {
    try {
      const blob = await downloadReport(report.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = report.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error("Could not download report", {
        description: error instanceof Error ? error.message : "Stored file unavailable",
      });
    }
  };

  const toggleFinding = (id: string) => {
    setSelectedFindingIds((prev) =>
      prev.includes(id) ? prev.filter((fid) => fid !== id) : [...prev, id],
    );
  };

  const searchTerm = findingSearch.trim().toLowerCase();
  const availableFindings = findings.filter(
    (finding) =>
      finding.id !== conversationHandoff?.finding_id &&
      (!searchTerm ||
        finding.title.toLowerCase().includes(searchTerm) ||
        finding.description.toLowerCase().includes(searchTerm)),
  );
  const reportItemCount = selectedFindingIds.length + (includeConversationDraft ? 1 : 0);
  const missingRequirements = [
    !clientName.trim() ? "client or target" : null,
    !engagementTitle.trim() ? "engagement title" : null,
    reportItemCount === 0 ? "at least one selected finding" : null,
  ].filter((item): item is string => Boolean(item));
  const canGenerate = missingRequirements.length === 0;

  const startGeneration = () => {
    setGenerationAttempted(true);
    if (!canGenerate) {
      toast.error("Report is not ready to generate", {
        description: `Complete: ${missingRequirements.join(", ")}.`,
      });
      return;
    }
    reportMutation.mutate();
  };
  const sections = [
    { name: "Executive Summary", count: "auto" },
    { name: "Scope & Methodology", count: "auto" },
    { name: "Findings Overview", count: `${reportItemCount} selected` },
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
            <div
              className={cn(
                "border p-4 transition-colors",
                includeConversationDraft
                  ? "border-brand-cyan bg-brand-cyan-soft/40"
                  : "border-border bg-[#fafafa]",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 flex-1 items-start gap-3">
                  <Checkbox
                    id="include-conversation-draft"
                    checked={includeConversationDraft}
                    onCheckedChange={(checked) => setIncludeConversationDraft(checked === true)}
                    className="mt-0.5"
                  />
                  <label htmlFor="include-conversation-draft" className="min-w-0 cursor-pointer">
                    <div className="flex items-center gap-2 text-sm font-semibold text-brand-navy">
                      <FileText className="h-4 w-4 text-brand-cyan" />
                      Current analysis
                      <span className="bg-white px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground ring-1 ring-border">
                        {includeConversationDraft ? "Selected" : "Excluded"}
                      </span>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                      {conversationHandoff.readiness.summary}
                    </p>
                  </label>
                </div>
                <div className="text-right">
                  <div className="text-lg font-semibold tabular-nums text-brand-navy">
                    {conversationHandoff.readiness.score.toFixed(1)} / 10
                  </div>
                  <div className="text-[10px] uppercase text-muted-foreground">
                    Assessment score
                  </div>
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

          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-brand-cyan" />
              <h2 className="text-lg font-semibold">Engagement details</h2>
            </div>
            <span className="text-xs text-muted-foreground">Required for the cover page</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Client / Target">
              <Input
                value={clientName}
                onChange={(e) => setClientName(e.target.value)}
                placeholder="e.g. Northwind Bank"
                aria-invalid={generationAttempted && !clientName.trim()}
                className={cn(generationAttempted && !clientName.trim() && "border-sev-critical")}
              />
            </Field>
            <Field label="Engagement title">
              <Input
                value={engagementTitle}
                onChange={(e) => setEngagementTitle(e.target.value)}
                placeholder="e.g. External PT Q1 2026"
                aria-invalid={generationAttempted && !engagementTitle.trim()}
                className={cn(
                  generationAttempted && !engagementTitle.trim() && "border-sev-critical",
                )}
              />
            </Field>
          </div>

          {/* Finding selector */}
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Saved findings ({selectedFindingIds.length} selected)
              </Label>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelectedFindingIds(availableFindings.map((f) => f.id))}
                >
                  Select all
                </Button>
                <Button variant="outline" size="sm" onClick={() => openFindingEditor(null)}>
                  <Plus className="mr-1.5 h-3.5 w-3.5" /> Add finding
                </Button>
              </div>
            </div>
            <div className="relative mt-2">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={findingSearch}
                onChange={(event) => setFindingSearch(event.target.value)}
                placeholder="Search title or description"
                className="pl-9"
              />
            </div>
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
                <div
                  key={f.id}
                  className={cn(
                    "flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors",
                    selectedFindingIds.includes(f.id) ? "bg-brand-cyan-soft" : "hover:bg-muted",
                  )}
                >
                  <button
                    onClick={() => toggleFinding(f.id)}
                    aria-label={`${selectedFindingIds.includes(f.id) ? "Exclude" : "Include"} ${f.title}`}
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
                  </button>
                  <button
                    onClick={() => toggleFinding(f.id)}
                    className="min-w-0 flex-1 truncate text-left"
                  >
                    {f.title}
                  </button>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {f.verdict || "pending"}
                  </span>
                  <button onClick={() => openFindingEditor(f)} aria-label={`Edit ${f.title}`}>
                    <Pencil className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground" />
                  </button>
                  <button
                    onClick={() => {
                      if (window.confirm(`Delete “${f.title}”?`))
                        removeFindingMutation.mutate(f.id);
                    }}
                    aria-label={`Delete ${f.title}`}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-sev-critical" />
                  </button>
                </div>
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

          <div className="border-t border-border pt-4">
            <div className="mb-4 grid gap-2 sm:grid-cols-3">
              <PipelineStep
                complete={reportItemCount > 0}
                number="01"
                label={`${reportItemCount} selected`}
              />
              <PipelineStep
                complete={Boolean(clientName.trim() && engagementTitle.trim())}
                number="02"
                label="Metadata complete"
              />
              <PipelineStep complete={canGenerate} number="03" label="Ready to export" />
            </div>
            {missingRequirements.length > 0 && (
              <div className="mb-3 flex items-start gap-2 bg-sev-medium/10 px-3 py-2 text-xs text-foreground ring-1 ring-inset ring-sev-medium/30">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sev-medium" />
                <span>
                  <strong>Before export:</strong> add {missingRequirements.join(" and ")}.
                </span>
              </div>
            )}
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {canGenerate
                  ? `${reportItemCount} report item${reportItemCount === 1 ? "" : "s"} will be included.`
                  : "Complete the highlighted requirements to generate the DOCX."}
              </span>
              <Button
                className="min-w-40"
                onClick={startGeneration}
                disabled={reportMutation.isPending}
              >
                {reportMutation.isPending ? (
                  <span className="h-4 w-4 mr-1.5 inline-block rounded-full border-2 border-white/30 border-t-white animate-spin" />
                ) : (
                  <FileDown className="h-4 w-4 mr-1.5" />
                )}
                {reportMutation.isPending ? "Generating..." : `Generate ${format.toUpperCase()}`}
              </Button>
            </div>
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
            <span className="font-semibold">Deterministic export:</span> only the conversation draft
            and saved findings you select are included. No new claims are generated during export.
          </div>
        </Card>

        <Card className="border-border bg-white p-6 shadow-soft xl:col-span-2">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-brand-navy">Report history</h2>
              <p className="text-xs text-muted-foreground">
                Previously generated deliverables remain available for download.
              </p>
            </div>
            <span className="text-xs tabular-nums text-muted-foreground">
              {reports.length} reports
            </span>
          </div>
          {reportsLoading ? (
            <ListSkeleton count={3} />
          ) : reports.length === 0 ? (
            <div className="border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              Generated reports will appear here.
            </div>
          ) : (
            <div className="divide-y divide-border border border-border">
              {reports.map((report) => (
                <div key={report.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <FileText className="h-4 w-4 text-brand-cyan" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{report.engagement_title}</div>
                    <div className="text-xs text-muted-foreground">
                      {report.client_name} ·{" "}
                      {report.finding_snapshot.length + (report.draft_snapshot ? 1 : 0)} items ·{" "}
                      {new Date(report.created_at).toLocaleString()}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => downloadHistoricalReport(report)}
                  >
                    <Download className="mr-1.5 h-3.5 w-3.5" /> Download
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      if (window.confirm(`Delete report “${report.engagement_title}”?`))
                        removeReportMutation.mutate(report.id);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Dialog
        open={editingFinding !== undefined}
        onOpenChange={(open) => !open && setEditingFinding(undefined)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingFinding ? "Edit finding" : "Add finding"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <Field label="Title">
              <Input
                value={findingTitle}
                onChange={(event) => setFindingTitle(event.target.value)}
              />
            </Field>
            <Field label="Description and evidence">
              <Textarea
                value={findingDescription}
                onChange={(event) => setFindingDescription(event.target.value)}
                rows={8}
              />
            </Field>
            <Field label="Verdict">
              <Select
                value={findingVerdict}
                onValueChange={(value) => setFindingVerdict(value as Verdict | "pending")}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pending">Pending</SelectItem>
                  <SelectItem value="confirmed">Confirmed</SelectItem>
                  <SelectItem value="likely">Likely</SelectItem>
                  <SelectItem value="insufficient">Insufficient</SelectItem>
                  <SelectItem value="false_positive">False positive</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <div className="flex justify-end gap-2 border-t border-border pt-4">
              <Button variant="ghost" onClick={() => setEditingFinding(undefined)}>
                Cancel
              </Button>
              <Button
                onClick={() => findingMutation.mutate()}
                disabled={
                  !findingTitle.trim() || !findingDescription.trim() || findingMutation.isPending
                }
              >
                {findingMutation.isPending ? "Saving..." : "Save finding"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
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

function PipelineStep({
  complete,
  number,
  label,
}: {
  complete: boolean;
  number: string;
  label: string;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-2 border px-3 py-2",
        complete ? "border-brand-cyan/40 bg-brand-cyan-soft/40" : "border-border bg-[#fafafa]",
      )}
    >
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center text-[10px] font-semibold tabular-nums",
          complete ? "bg-brand-cyan text-white" : "bg-muted text-muted-foreground",
        )}
      >
        {complete ? <CheckCircle className="h-3.5 w-3.5" /> : number}
      </span>
      <span className="min-w-0 truncate text-xs font-medium">{label}</span>
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
