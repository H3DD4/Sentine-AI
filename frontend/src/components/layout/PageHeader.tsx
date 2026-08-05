import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  className,
  compact = false,
  dense = false,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  compact?: boolean;
  dense?: boolean;
}) {
  return (
    <header className={cn("border-b border-border bg-white", className)}>
      <div
        className={cn(
          "flex flex-col px-5 md:flex-row md:items-end md:justify-between md:px-8 lg:px-10",
          dense
            ? "gap-2 py-2.5 md:items-center md:py-3"
            : compact
              ? "gap-3 py-4 md:py-5"
              : "gap-5 py-8 md:py-10",
        )}
      >
        <div className="max-w-3xl">
          {eyebrow && <div className="mb-2 text-sm font-semibold text-brand-cyan">{eyebrow}</div>}
          <h1
            className={cn(
              "font-normal leading-[1.1] text-foreground",
              dense
                ? "text-xl md:text-[1.375rem]"
                : compact
                  ? "text-2xl md:text-[2rem]"
                  : "text-[2.125rem] md:text-[2.875rem]",
            )}
          >
            {title}
          </h1>
          {description && (
            <p
              className={cn(
                "max-w-2xl leading-relaxed text-muted-foreground",
                compact ? "mt-1 text-sm" : "mt-3 text-base md:text-[1.0625rem]",
              )}
            >
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}
