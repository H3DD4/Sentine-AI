import { createFileRoute, Link } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { SeverityBadge, VerdictBadge } from "@/components/brand/Badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteConversation, listConversations, listFindings, listEngagements } from "@/lib/api";
import type { Finding, Engagement, Severity } from "@/lib/api";
import {
  StatsGridSkeleton,
  EngagementListSkeleton,
  TableSkeleton,
} from "@/components/ui/loading-skeletons";
import { toast } from "sonner";
import {
  Plus,
  ArrowUpRight,
  Activity,
  ShieldAlert,
  Target,
  Zap,
  MessageSquare,
  Trash2,
} from "lucide-react";

export const Route = createFileRoute("/")({ component: Dashboard });

function Dashboard() {
  const queryClient = useQueryClient();
  const { data: findings = [], isLoading: findingsLoading } = useQuery<Finding[]>({
    queryKey: ["findings"],
    queryFn: () => listFindings(0),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    staleTime: 10_000,
  });

  const { data: engagementsData = [], isLoading: engagementsLoading } = useQuery<Engagement[]>({
    queryKey: ["engagements"],
    queryFn: listEngagements,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    staleTime: 10_000,
  });

  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });

  const activeEngagements = engagementsData.filter((e) => e.status === "active").length;
  const criticalFindings = findings.filter((f) => f.verdict === "confirmed").length;
  const aiValidated24h = findings.filter((f) => {
    // A finding with neither timestamp cannot be dated, so it cannot be
    // counted in a 24-hour window. `new Date(null)` silently yields the epoch,
    // which would have quietly excluded it anyway — but by accident.
    const stamp = f.updated_at || f.created_at;
    if (!stamp) return false;
    return Date.now() - new Date(stamp).getTime() < 24 * 60 * 60 * 1000;
  }).length;
  const avgConfidence =
    findings.length > 0
      ? Math.round(
          (findings.reduce((sum, f) => sum + (f.confidence || 0), 0) / findings.length) * 100,
        )
      : 0;

  const stats = [
    {
      label: "Active engagements",
      value: String(activeEngagements),
      delta: `${engagementsData.length} total`,
      icon: Target,
      accent: "brand-cyan" as const,
    },
    {
      label: "Findings open",
      value: String(findings.length),
      delta: `${criticalFindings} critical`,
      icon: ShieldAlert,
      accent: "sev-critical" as const,
    },
    {
      label: "AI validated (24h)",
      value: String(aiValidated24h),
      delta: `${avgConfidence}% avg. confidence`,
      icon: Zap,
      accent: "verdict-confirmed" as const,
    },
    {
      label: "Engagements total",
      value: String(engagementsData.length),
      delta: activeEngagements > 0 ? `${activeEngagements} active` : "0 active",
      icon: Activity,
      accent: "brand-navy" as const,
    },
  ];

  const isLoading = findingsLoading || engagementsLoading;

  const verdictToSeverity = (v: string | null): Severity => {
    switch (v) {
      case "confirmed":
        return "critical";
      case "likely":
        return "high";
      case "insufficient":
        return "medium";
      case "false_positive":
        return "info";
      default:
        return "info";
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <PageHeader
          eyebrow="Console / Overview"
          title="Red Team Operations"
          description="Loading your workspace..."
        />
        <div className="px-6 md:px-10 py-8 space-y-8">
          <StatsGridSkeleton />
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <div className="xl:col-span-1">
              <EngagementListSkeleton />
            </div>
            <div className="xl:col-span-2">
              <TableSkeleton rows={5} columns={5} />
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="Security assistant"
        title="Engagement overview"
        description="A concise view of current engagements and recently validated findings."
        actions={
          <>
            <Button variant="outline" asChild>
              <Link to="/knowledge">Knowledge base</Link>
            </Button>
            <Button asChild>
              <Link to="/chat" search={{ new: true } as never}>
                <Plus className="h-4 w-4 mr-1.5" />
                New Finding
              </Link>
            </Button>
          </>
        }
      />

      <div className="space-y-8 px-5 py-7 md:px-8 lg:px-10">
        {/* KPI row */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {stats.map((s) => (
            <Card key={s.label} className="relative overflow-hidden border-border bg-white p-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-xs font-semibold text-muted-foreground">{s.label}</div>
                  <div className="mt-2 text-3xl font-light">{s.value}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{s.delta}</div>
                </div>
                <div
                  className="rounded-sm p-2 ring-1 ring-inset"
                  style={{
                    background: `color-mix(in oklab, var(--${s.accent}) 12%, transparent)`,
                    borderColor: `var(--${s.accent})`,
                  }}
                >
                  <s.icon className="h-4 w-4" style={{ color: `var(--${s.accent})` }} />
                </div>
              </div>
            </Card>
          ))}
        </div>

        {/* Grid: engagements + findings */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <Card className="border-border bg-white p-5 shadow-soft xl:col-span-1">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-xs font-semibold text-brand-cyan">Active</div>
                <h2 className="text-lg font-semibold">Engagements</h2>
              </div>
            </div>
            <div className="space-y-3">
              {engagementsData.length === 0 && (
                <div className="text-center py-8 text-sm text-muted-foreground">
                  No engagements yet. Create one via the API.
                </div>
              )}
              {engagementsData.slice(0, 6).map((e) => (
                <div
                  key={e.id}
                  className="group border border-border p-3 transition-colors hover:border-brand-cyan"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold truncate">{e.client_name}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {e.code} · {e.scope}
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-semibold uppercase tracking-wider rounded px-1.5 py-0.5 ring-1 ring-inset
                      ${
                        e.status === "active"
                          ? "text-verdict-confirmed ring-verdict-confirmed/40 bg-verdict-confirmed/10"
                          : e.status === "reporting"
                            ? "text-brand-cyan ring-brand-cyan/40 bg-brand-cyan/10"
                            : "text-muted-foreground ring-border bg-muted"
                      }`}
                    >
                      {e.status}
                    </span>
                  </div>
                  <div className="mt-3 flex items-center gap-3">
                    <Progress value={e.progress} className="h-1.5 flex-1" />
                    <span className="w-10 text-right text-[11px] tabular-nums text-muted-foreground">
                      {Math.round(e.progress)}%
                    </span>
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>
                      Lead <span className="text-foreground">{e.lead}</span>
                    </span>
                    <span>{e.findings_count} findings</span>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="border-border bg-white p-5 shadow-soft xl:col-span-2">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-xs font-semibold text-brand-cyan">Recent activity</div>
                <h2 className="text-lg font-semibold">Recent findings</h2>
              </div>
              <Button variant="ghost" size="sm" asChild>
                <Link to="/chat">
                  Open analysis <ArrowUpRight className="h-3.5 w-3.5 ml-1" />
                </Link>
              </Button>
            </div>

            <div className="overflow-x-auto border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-[11px] uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="text-left py-2.5 px-3 font-medium">Finding</th>
                    <th className="text-left py-2.5 px-3 font-medium">Sev</th>
                    <th className="text-left py-2.5 px-3 font-medium">Verdict</th>
                    <th className="text-right py-2.5 px-3 font-medium">Conf.</th>
                    <th className="text-right py-2.5 px-3 font-medium">Updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {findings.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-12 text-center text-sm text-muted-foreground">
                        No findings yet. Go to{" "}
                        <Link to="/chat" className="text-brand-cyan underline">
                          Analysis
                        </Link>{" "}
                        to create one.
                      </td>
                    </tr>
                  )}
                  {findings.slice(0, 10).map((f) => (
                    <tr key={f.id} className="hover:bg-muted/30 transition-colors">
                      <td className="py-3 px-3">
                        <div className="flex flex-col">
                          <span className="font-medium leading-snug">{f.title}</span>
                          <span className="max-w-[380px] truncate text-[11px] text-muted-foreground">
                            {f.matched_cves?.length ? f.matched_cves.join(", ") : ""}
                            {f.matched_techniques?.length
                              ? ` · ${f.matched_techniques.slice(0, 2).join(", ")}`
                              : ""}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 px-3">
                        <SeverityBadge severity={verdictToSeverity(f.verdict)} />
                      </td>
                      <td className="py-3 px-3">
                        {f.verdict ? (
                          <VerdictBadge verdict={f.verdict} size="sm" />
                        ) : (
                          <span className="text-[11px] text-muted-foreground">not validated</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-right text-xs tabular-nums">
                        <span
                          className={
                            (f.confidence || 0) >= 0.85
                              ? "text-verdict-confirmed"
                              : (f.confidence || 0) >= 0.6
                                ? "text-verdict-review"
                                : "text-muted-foreground"
                          }
                        >
                          {f.confidence ? Math.round(f.confidence * 100) : "—"}%
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right text-xs text-muted-foreground">
                        {f.updated_at ? new Date(f.updated_at).toLocaleDateString() : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <Card className="border-border bg-white p-5 shadow-soft">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="text-xs font-semibold text-brand-cyan">Workspace memory</div>
              <h2 className="text-lg font-semibold">Previous analyses</h2>
            </div>
            <Button size="sm" asChild>
              <Link to="/chat" search={{ new: true } as never}>
                <Plus className="mr-1.5 h-4 w-4" />
                New analysis
              </Link>
            </Button>
          </div>
          {conversations.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">
              Your saved conversations will appear here.
            </p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {conversations.slice(0, 9).map((conversation) => (
                <div
                  key={conversation.id}
                  className="group flex items-start gap-3 border border-border p-3 hover:border-brand-cyan"
                >
                  <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-brand-cyan" />
                  <Link
                    to="/chat"
                    search={{ conversation: conversation.id } as never}
                    className="min-w-0 flex-1"
                  >
                    <div className="truncate text-sm font-semibold">{conversation.title}</div>
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      Updated {new Date(conversation.updated_at).toLocaleString()}
                    </div>
                    {conversation.finding_id && (
                      <div className="mt-1 text-[11px] text-verdict-confirmed">
                        Validated finding attached
                      </div>
                    )}
                  </Link>
                  <button
                    className="text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-destructive"
                    onClick={async () => {
                      await deleteConversation(conversation.id);
                      queryClient.invalidateQueries({ queryKey: ["conversations"] });
                    }}
                    aria-label={`Delete ${conversation.title}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
