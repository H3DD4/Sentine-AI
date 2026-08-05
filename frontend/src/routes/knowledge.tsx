import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useState, useCallback, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { searchKnowledgeBase, listKnowledgeBase, getSources, reindexSource } from "@/lib/api";
import type { KBEntry, KBSearchResponse, SourcesResponse } from "@/lib/api";
import { SourceBadge, ProvenanceBar } from "@/components/brand/SourceBadges";
import { CardGridSkeleton } from "@/components/ui/loading-skeletons";
import { Search, BookOpen, ExternalLink, RefreshCw, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export const Route = createFileRoute("/knowledge")({ component: KnowledgePage });

const ALL = "__all__";

function KnowledgePage() {
  const [q, setQ] = useState("");
  // `null` means "not chosen yet" — resolved to the first live source below,
  // so the browse tab opens on a corpus that actually has something in it.
  const [selected, setSelected] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: health } = useQuery<SourcesResponse>({
    queryKey: ["kb-sources"],
    queryFn: () => getSources(),
    refetchInterval: 60_000,
  });

  const sources = useMemo(() => health?.sources ?? [], [health]);
  const searchMode = q.trim().length >= 2;

  const browseSource = useMemo(() => {
    if (selected && selected !== ALL) return selected;
    const live = sources.find((s) => s.row_count > 0);
    return live?.source ?? sources[0]?.source ?? "nvd";
  }, [selected, sources]);

  // Browsing is per-source by construction: the sources hold different kinds of
  // document with different natural orderings, so an interleaved "all" list
  // would be ordered by nothing in particular and mislead about coverage.
  const { data: listing, isLoading: listLoading } = useQuery({
    queryKey: ["kb-entries", browseSource],
    queryFn: () => listKnowledgeBase(browseSource, 0, 60),
    enabled: !searchMode && sources.length > 0,
  });

  // Search is federated — the source chips filter which corpora are queried.
  const { data: search, isLoading: searchLoading } = useQuery<KBSearchResponse>({
    queryKey: ["kb-search", q, selected],
    queryFn: () =>
      searchKnowledgeBase(q, undefined, selected && selected !== ALL ? [selected] : undefined, 24),
    enabled: searchMode,
  });

  const reindex = useMutation({
    mutationFn: (key: string) => reindexSource(key),
    onSuccess: (res) => {
      toast.success(`Reindexed ${res.source}`, {
        description: `${res.indexed} of ${res.considered} documents re-embedded.`,
      });
      queryClient.invalidateQueries({ queryKey: ["kb-sources"] });
      queryClient.invalidateQueries({ queryKey: ["kb-entries"] });
    },
    onError: (err) =>
      toast.error("Reindex failed", {
        description: err instanceof Error ? err.message : "Unknown error",
      }),
  });

  const items: KBEntry[] = searchMode
    ? (search?.results ?? []).map((r) => ({
        source: r.source,
        doc_id: r.id,
        title: r.title,
        description: r.description,
        score: r.score,
        ref_urls: r.url ? [r.url] : [],
      }))
    : (listing?.entries ?? []);

  const isLoading = searchMode ? searchLoading : listLoading;

  const labelFor = useCallback(
    (key: string) => sources.find((s) => s.source === key)?.label ?? key,
    [sources],
  );

  return (
    <AppShell>
      <PageHeader
        eyebrow="Knowledge base"
        title="Security knowledge sources"
        description="Search public vulnerability data, MITRE ATT&CK, prior engagement findings, and internal notes while keeping every source clearly identified."
      />

      <div className="space-y-6 px-5 py-7 md:px-8 lg:px-10">
        {/* Source health: the state of every corpus, always on screen. */}
        <Card className="space-y-3 border-border bg-white p-5 shadow-soft">
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-brand-navy">Source availability</div>
            {health && (
              <div className="text-[11px] tabular-nums text-muted-foreground">
                {health.usable_count}/{health.total_count} searchable
              </div>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
            {sources.map((s) => (
              <div
                key={s.source}
                className="flex items-center justify-between gap-2 border border-border bg-[#fafafa] px-3 py-2.5"
              >
                <div className="min-w-0">
                  <SourceBadge source={s} showCount={false} />
                  <div className="mt-1 text-[10px] tabular-nums text-muted-foreground">
                    {s.vector_count.toLocaleString()} vectors · {s.row_count.toLocaleString()} rows
                    {s.unsynced_count > 0 && (
                      <span className="text-verdict-review">
                        {" "}
                        · {s.unsynced_count.toLocaleString()} unindexed
                      </span>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  title="Re-embed this source's documents"
                  disabled={reindex.isPending}
                  onClick={() => reindex.mutate(s.source)}
                >
                  {reindex.isPending && reindex.variables === s.source ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            ))}
          </div>
        </Card>

        <Card className="border-border bg-white p-5 shadow-soft">
          <div className="flex flex-col md:flex-row md:items-center gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search CVE, technique, keyword…"
                className="h-11 bg-white pl-9 text-sm"
              />
            </div>
            <div className="flex flex-wrap gap-1.5">
              <FilterChip
                active={!selected || selected === ALL}
                onClick={() => setSelected(ALL)}
                label="All"
              />
              {sources.map((s) => (
                <FilterChip
                  key={s.source}
                  active={selected === s.source}
                  onClick={() => setSelected(s.source)}
                  label={s.label}
                  muted={s.status !== "ok"}
                />
              ))}
            </div>
          </div>
          {searchMode && !selected && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              Searching every available source. Pick one to narrow the query.
            </p>
          )}
          {!searchMode && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              Browsing <span className="font-semibold">{labelFor(browseSource)}</span>. Type at
              least 2 characters to search across sources.
            </p>
          )}
        </Card>

        {/* Search results carry their own provenance: a short list means
            something different when a corpus was unreachable. */}
        {searchMode && search && (
          <ProvenanceBar
            reports={search.sources}
            provenance={search.provenance}
            degraded={search.degraded}
            className="mt-0 border-t-0 pt-0"
          />
        )}

        <div className="flex items-center justify-between">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {isLoading ? "Loading…" : `${items.length} ${searchMode ? "results" : "entries"}`}
          </div>
        </div>

        {isLoading ? (
          <CardGridSkeleton count={6} />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {items.map((entry) => (
              <Card
                key={`${entry.source}:${entry.doc_id}`}
                className="group relative overflow-hidden border-border bg-white p-5 transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-brand-cyan hover:shadow-soft"
              >
                <div
                  className="absolute right-0 top-0 h-1 w-20 opacity-0 transition-opacity group-hover:opacity-100"
                  style={{
                    background:
                      entry.cvss_v3 && entry.cvss_v3 >= 7.0
                        ? "var(--sev-critical)"
                        : "var(--brand-cyan)",
                  }}
                />

                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="truncate text-[11px] font-semibold text-brand-cyan">
                    {entry.doc_id}
                  </div>
                  {entry.cvss_v3 ? (
                    <span
                      className={cn(
                        "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ring-1 ring-inset",
                        entry.cvss_v3 >= 9.0
                          ? "text-sev-critical ring-sev-critical/40 bg-sev-critical/10"
                          : entry.cvss_v3 >= 7.0
                            ? "text-sev-high ring-sev-high/40 bg-sev-high/10"
                            : entry.cvss_v3 >= 4.0
                              ? "text-sev-medium ring-sev-medium/40 bg-sev-medium/10"
                              : "text-muted-foreground ring-border bg-muted",
                      )}
                    >
                      CVSS {entry.cvss_v3.toFixed(1)}
                    </span>
                  ) : entry.score !== undefined ? (
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                      {entry.score.toFixed(3)}
                    </span>
                  ) : null}
                </div>

                <h3 className="text-sm font-semibold leading-snug mb-2">{entry.title}</h3>
                <p className="text-xs text-muted-foreground leading-relaxed mb-4 line-clamp-3">
                  {entry.description}
                </p>

                <div className="flex flex-wrap gap-1 mb-3">
                  {entry.mitre_techniques?.slice(0, 3).map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                  {entry.cwe && <Tag>{entry.cwe}</Tag>}
                  {entry.tactics?.slice(0, 2).map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                  {entry.tags?.slice(0, 2).map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                </div>

                {/* Every card names its source. Which corpus a claim came from
                    changes how much weight it carries in a client report. */}
                <div className="flex items-center justify-between border-t border-border pt-3 text-[11px]">
                  <span className="text-muted-foreground">{labelFor(entry.source)}</span>
                  <span className="flex items-center gap-2">
                    {entry.synced === false && (
                      <span className="text-verdict-review" title="Not yet indexed for search">
                        unindexed
                      </span>
                    )}
                    {entry.ref_urls?.[0] && (
                      <a
                        href={entry.ref_urls[0]}
                        target="_blank"
                        rel="noreferrer"
                        className="text-brand-cyan flex items-center gap-1 hover:underline"
                      >
                        Source <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </span>
                </div>
              </Card>
            ))}
          </div>
        )}

        {!isLoading && items.length === 0 && (
          <div className="text-center py-16">
            <BookOpen className="h-8 w-8 mx-auto text-muted-foreground mb-3" />
            <p className="text-sm text-muted-foreground">
              {searchMode
                ? "No documents matched, across the sources that answered."
                : `${labelFor(browseSource)} has no documents yet.`}
            </p>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function FilterChip({
  active,
  onClick,
  label,
  muted,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  muted?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "h-9 rounded-sm px-3 text-xs font-semibold transition-colors",
        active
          ? "bg-brand-navy text-white"
          : "border border-border bg-white text-muted-foreground hover:border-brand-cyan hover:text-brand-navy",
        // A source that can't be searched is still selectable — the empty
        // result then explains itself via the provenance bar.
        !active && muted && "opacity-50",
      )}
    >
      {label}
    </button>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
      {children}
    </span>
  );
}
