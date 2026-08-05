import { Link, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  MessagesSquare,
  FileText,
  BookOpen,
  Settings,
  ShieldCheck,
  Menu,
} from "lucide-react";
import type { ReactNode } from "react";
import { Logo } from "@/components/brand/Logo";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";

const nav = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/chat", label: "Analysis", icon: MessagesSquare },
  { to: "/report", label: "Report Builder", icon: FileText },
  { to: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { location } = useRouterState();

  return (
    <div className="relative flex min-h-screen flex-col bg-background text-foreground md:flex-row">
      {/* Mobile Nav */}
      <div className="sticky top-0 z-50 flex h-[4.75rem] items-center justify-between border-b border-sidebar-border bg-white px-4 shadow-sm md:hidden">
        <Logo />
        <Sheet>
          <SheetTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Open navigation"
              className="text-brand-navy"
            >
              <Menu className="h-5 w-5" />
            </Button>
          </SheetTrigger>
          <SheetContent
            side="left"
            className="w-72 border-r border-sidebar-border bg-white p-0 text-sidebar-foreground"
          >
            <SheetTitle className="sr-only">Navigation Menu</SheetTitle>
            <div className="flex h-24 items-center border-b border-sidebar-border px-5">
              <Logo />
            </div>
            <nav className="flex-1 space-y-1 px-3 py-5" aria-label="Main navigation">
              {nav.map(({ to, label, icon: Icon }) => {
                const active = location.pathname === to;
                return (
                  <Link
                    key={to}
                    to={to}
                    className={cn(
                      "group flex min-h-12 items-center gap-3 rounded-sm px-3 text-[15px] font-semibold transition-colors",
                      active
                        ? "bg-sidebar-accent text-brand-navy"
                        : "text-sidebar-foreground hover:bg-muted hover:text-brand-navy",
                    )}
                  >
                    <Icon
                      className={cn(
                        "h-[18px] w-[18px]",
                        active ? "text-brand-cyan" : "text-muted-foreground",
                      )}
                      strokeWidth={1.8}
                    />
                    <span>{label}</span>
                  </Link>
                );
              })}
            </nav>
            <div className="absolute inset-x-0 bottom-0 border-t border-sidebar-border p-4">
              <div className="text-xs text-muted-foreground">
                Internal security workspace · v2.4.1
              </div>
            </div>
          </SheetContent>
        </Sheet>
      </div>

      <aside className="sticky top-0 hidden h-screen w-64 flex-col border-r border-sidebar-border bg-white text-sidebar-foreground md:flex">
        <div className="flex h-24 items-center border-b border-sidebar-border px-5">
          <Logo />
        </div>

        <nav className="flex-1 space-y-1 px-3 py-5" aria-label="Main navigation">
          {nav.map(({ to, label, icon: Icon }) => {
            const active = location.pathname === to;
            return (
              <Link
                key={to}
                to={to}
                className={cn(
                  "group flex min-h-12 items-center gap-3 rounded-sm px-3 text-[15px] font-semibold transition-colors",
                  active
                    ? "bg-sidebar-accent text-brand-navy"
                    : "text-sidebar-foreground hover:bg-muted hover:text-brand-navy",
                )}
              >
                <Icon
                  className={cn(
                    "h-[18px] w-[18px]",
                    active ? "text-brand-cyan" : "text-muted-foreground",
                  )}
                  strokeWidth={1.8}
                />
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="space-y-3 border-t border-sidebar-border p-4">
          <div className="rounded-sm bg-muted px-3 py-2.5">
            <div className="flex items-center gap-2 text-xs font-semibold">
              <ShieldCheck className="h-4 w-4 text-verdict-confirmed" />
              System status
            </div>
            <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
              <span className="relative flex h-2 w-2">
                <span className="relative inline-flex h-2 w-2 rounded-full bg-verdict-confirmed" />
              </span>
              All validators online
            </div>
          </div>
          <div className="text-[11px] text-muted-foreground">Internal workspace · v2.4.1</div>
        </div>
      </aside>

      <main className="flex-1 min-w-0">{children}</main>
    </div>
  );
}
