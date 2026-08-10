import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listConversations,
  deleteConversation,
  updateConversation,
} from "@/lib/api";
import type { AnalysisConversation } from "@/lib/api";
import { toast } from "sonner";
import { useState } from "react";
import {
  MessageSquare,
  Trash2,
  Plus,
  Search,
  Clock,
  CheckCircle2,
  Pencil,
  Check,
  X,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/conversations")({ component: ConversationsPage });

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const m = Math.floor(diff / 60_000);
  const h = Math.floor(diff / 3_600_000);
  const d = Math.floor(diff / 86_400_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (h < 24) return `${h}h ago`;
  if (d < 7) return `${d}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

function ConversationCard({
  conversation,
  onDelete,
  onRename,
}: {
  conversation: AnalysisConversation;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const msgCount = Array.isArray(conversation.messages) ? conversation.messages.length : 0;

  function commitRename() {
    if (title.trim() && title.trim() !== conversation.title) {
      onRename(conversation.id, title.trim());
    } else {
      setTitle(conversation.title);
    }
    setEditing(false);
  }

  return (
    <Card
      className="group relative flex flex-col gap-3 border border-border bg-white p-4 transition-all hover:border-brand-cyan hover:shadow-md cursor-pointer"
      onClick={() => {
        if (!editing)
          navigate({ to: "/chat", search: { conversation: conversation.id } as never });
      }}
    >
      {/* Header row */}
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-brand-cyan/10 text-brand-cyan">
          <MessageSquare className="h-4 w-4" strokeWidth={1.8} />
        </div>

        <div className="min-w-0 flex-1">
          {editing ? (
            <div
              className="flex items-center gap-1"
              onClick={(e) => e.stopPropagation()}
            >
              <input
                autoFocus
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") {
                    setTitle(conversation.title);
                    setEditing(false);
                  }
                }}
                className="flex-1 rounded border border-brand-cyan bg-transparent px-2 py-0.5 text-sm font-semibold outline-none"
              />
              <button
                onClick={commitRename}
                className="p-1 text-verdict-confirmed hover:bg-muted rounded"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => { setTitle(conversation.title); setEditing(false); }}
                className="p-1 text-muted-foreground hover:bg-muted rounded"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <div className="truncate text-sm font-semibold leading-snug text-foreground">
              {conversation.title}
            </div>
          )}

          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
            <Clock className="h-3 w-3 shrink-0" />
            <span>{timeAgo(conversation.updated_at)}</span>
            <span className="text-border">·</span>
            <span>{msgCount} message{msgCount !== 1 ? "s" : ""}</span>
          </div>
        </div>

        {/* Action buttons — visible on hover */}
        <div
          className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            title="Rename"
            className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-brand-navy transition-colors"
            onClick={() => setEditing(true)}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            title="Delete"
            className="rounded p-1.5 text-muted-foreground hover:bg-red-50 hover:text-destructive transition-colors"
            onClick={() => onDelete(conversation.id)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Chips */}
      <div className="flex flex-wrap items-center gap-1.5">
        {conversation.finding_id && (
          <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-verdict-confirmed ring-1 ring-inset ring-verdict-confirmed/30 bg-verdict-confirmed/8">
            <CheckCircle2 className="h-3 w-3" />
            Finding attached
          </span>
        )}
        {msgCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground ring-1 ring-inset ring-border bg-muted">
            {msgCount} msg
          </span>
        )}
      </div>

      {/* Open arrow */}
      <div className="absolute right-3 bottom-3 opacity-0 transition-opacity group-hover:opacity-100 text-brand-cyan">
        <ChevronRight className="h-4 w-4" />
      </div>
    </Card>
  );
}

function ConversationsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");

  const { data: conversations = [], isLoading } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
    refetchInterval: 30_000,
  });

  const deleteMut = useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Conversation deleted");
    },
    onError: () => toast.error("Failed to delete"),
  });

  const renameMut = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      updateConversation(id, { title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Renamed");
    },
    onError: () => toast.error("Failed to rename"),
  });

  const filtered = conversations.filter((c) =>
    c.title.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <AppShell>
      <PageHeader
        eyebrow="Workspace memory"
        title="Chat history"
        description="Browse, resume, rename or delete your past analysis sessions."
        actions={
          <Button asChild>
            <Link to="/chat" search={{ new: true } as never}>
              <Plus className="mr-1.5 h-4 w-4" />
              New analysis
            </Link>
          </Button>
        }
      />

      <div className="px-5 py-7 md:px-8 lg:px-10 space-y-6">
        {/* Search bar */}
        <div className="relative max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="Search conversations…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-sm border border-border bg-white py-2 pl-9 pr-4 text-sm outline-none focus:border-brand-cyan focus:ring-1 focus:ring-brand-cyan/30 transition-all"
          />
        </div>

        {/* Stats strip */}
        {!isLoading && (
          <div className="flex items-center gap-4 text-sm text-muted-foreground">
            <span>
              <span className="font-semibold text-foreground">{conversations.length}</span>{" "}
              total session{conversations.length !== 1 ? "s" : ""}
            </span>
            {search && (
              <span>
                <span className="font-semibold text-foreground">{filtered.length}</span>{" "}
                matching
              </span>
            )}
          </div>
        )}

        {/* Conversation grid */}
        {isLoading ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-28 animate-pulse rounded border border-border bg-muted"
              />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 rounded border border-dashed border-border py-20 text-center">
            <MessageSquare className="h-10 w-10 text-muted-foreground/40" strokeWidth={1.2} />
            <div>
              <p className="font-semibold text-foreground">
                {search ? "No conversations match" : "No conversations yet"}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {search
                  ? "Try a different search term."
                  : "Start a new analysis — it will appear here automatically."}
              </p>
            </div>
            {!search && (
              <Button asChild size="sm">
                <Link to="/chat" search={{ new: true } as never}>
                  <Plus className="mr-1.5 h-3.5 w-3.5" />
                  New analysis
                </Link>
              </Button>
            )}
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((c) => (
              <ConversationCard
                key={c.id}
                conversation={c}
                onDelete={(id) => {
                  if (confirm("Delete this conversation? This cannot be undone.")) {
                    deleteMut.mutate(id);
                  }
                }}
                onRename={(id, title) => renameMut.mutate({ id, title })}
              />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
