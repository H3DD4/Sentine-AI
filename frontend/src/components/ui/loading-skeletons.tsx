/**
 * Sentinel.AI — Reusable Loading Skeletons
 * Used across all pages for consistent loading UX.
 */

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

// ─── Stats Card Skeleton ─────────────────────────────────────────────

export function StatsCardSkeleton() {
  return (
    <Card className="relative overflow-hidden p-5 bg-grad-panel border-border shadow-soft">
      <div className="flex items-start justify-between">
        <div className="space-y-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-16" />
          <Skeleton className="h-3 w-20" />
        </div>
        <Skeleton className="h-8 w-8 rounded-lg" />
      </div>
    </Card>
  );
}

// ─── Stats Grid Skeleton ─────────────────────────────────────────────

export function StatsGridSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <StatsCardSkeleton key={i} />
      ))}
    </div>
  );
}

// ─── Table Row Skeleton ──────────────────────────────────────────────

export function TableRowSkeleton({ columns = 5 }: { columns?: number }) {
  return (
    <tr className="border-b border-border">
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="py-3 px-3">
          <Skeleton className={cn("h-4", i === 0 ? "w-48" : i === columns - 1 ? "w-16 ml-auto" : "w-20")} />
        </td>
      ))}
    </tr>
  );
}

// ─── Table Skeleton ──────────────────────────────────────────────────

export function TableSkeleton({ rows = 5, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            {Array.from({ length: columns }).map((_, i) => (
              <th key={i} className="py-2.5 px-3">
                <Skeleton className="h-3 w-16" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {Array.from({ length: rows }).map((_, i) => (
            <TableRowSkeleton key={i} columns={columns} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Card Skeleton ────────────────────────────────────────────────────

export function CardSkeleton() {
  return (
    <Card className="p-5 bg-card border-border">
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-2">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-4 w-14 rounded" />
        </div>
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
        <div className="flex gap-1.5">
          <Skeleton className="h-4 w-16 rounded" />
          <Skeleton className="h-4 w-12 rounded" />
        </div>
        <div className="flex items-center justify-between pt-3 border-t border-border">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-12" />
        </div>
      </div>
    </Card>
  );
}

// ─── Card Grid Skeleton ──────────────────────────────────────────────

export function CardGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <CardSkeleton key={i} />
      ))}
    </div>
  );
}

// ─── Chat Message Skeleton ───────────────────────────────────────────

export function ChatMessageSkeleton({ isUser = false }: { isUser?: boolean }) {
  return (
    <div className={cn("flex gap-3", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <Skeleton className="h-8 w-8 rounded-lg shrink-0" />
      )}
      <div className={cn("max-w-[76%]", isUser && "order-1")}>
        <Skeleton className="h-3 w-16 mb-1" />
        <div className={cn(
          "rounded-2xl px-4 py-3",
          isUser ? "bg-grad-brand" : "bg-card border border-border"
        )}>
          <div className="space-y-2">
            <Skeleton className={cn("h-4 w-full", isUser ? "bg-white/30" : "")} />
            <Skeleton className={cn("h-4 w-5/6", isUser ? "bg-white/30" : "")} />
            <Skeleton className={cn("h-4 w-2/3", isUser ? "bg-white/30" : "")} />
          </div>
        </div>
      </div>
      {isUser && (
        <Skeleton className="h-8 w-8 rounded-lg shrink-0" />
      )}
    </div>
  );
}

// ─── Chat Skeleton ────────────────────────────────────────────────────

export function ChatSkeleton() {
  return (
    <div className="space-y-6">
      <ChatMessageSkeleton />
      <ChatMessageSkeleton isUser />
      <ChatMessageSkeleton />
    </div>
  );
}

// ─── List Item Skeleton ──────────────────────────────────────────────

export function ListItemSkeleton() {
  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-md">
      <Skeleton className="h-4 w-4 rounded shrink-0" />
      <Skeleton className="h-4 flex-1" />
      <Skeleton className="h-3 w-14 shrink-0" />
    </div>
  );
}

// ─── List Skeleton ────────────────────────────────────────────────────

export function ListSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-1.5 rounded-lg border border-border p-2">
      {Array.from({ length: count }).map((_, i) => (
        <ListItemSkeleton key={i} />
      ))}
    </div>
  );
}

// ─── Form Field Skeleton ─────────────────────────────────────────────

export function FormFieldSkeleton() {
  return (
    <div className="space-y-1.5">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-10 w-full rounded-lg" />
    </div>
  );
}

// ─── Form Skeleton ────────────────────────────────────────────────────

export function FormSkeleton({ fields = 4 }: { fields?: number }) {
  return (
    <Card className="p-6 bg-card border-border">
      <div className="space-y-5">
        <div className="flex items-start gap-3">
          <Skeleton className="h-8 w-8 rounded-lg" />
          <div className="space-y-1.5 flex-1">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-3 w-64" />
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Array.from({ length: fields }).map((_, i) => (
            <FormFieldSkeleton key={i} />
          ))}
        </div>
        <div className="pt-2 border-t border-border">
          <Skeleton className="h-10 w-32 ml-auto rounded-lg" />
        </div>
      </div>
    </Card>
  );
}

// ─── Engagement Card Skeleton ────────────────────────────────────────

export function EngagementCardSkeleton() {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <Skeleton className="h-4 w-32 mb-1" />
          <Skeleton className="h-3 w-48" />
        </div>
        <Skeleton className="h-4 w-16 rounded" />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Skeleton className="h-1.5 flex-1 rounded-full" />
        <Skeleton className="h-3 w-10" />
      </div>
      <div className="mt-2 flex items-center justify-between">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-3 w-16" />
      </div>
    </div>
  );
}

// ─── Engagement List Skeleton ────────────────────────────────────────

export function EngagementListSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <EngagementCardSkeleton key={i} />
      ))}
    </div>
  );
}
