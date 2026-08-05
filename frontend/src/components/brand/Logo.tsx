import logo from "@/assets/forvis-mazars-logo.png";
import { cn } from "@/lib/utils";

export function Logo({
  className,
  showWordmark = true,
}: {
  className?: string;
  showWordmark?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <img src={logo} alt="Forvis Mazars" className="h-8 w-auto object-contain" />
      {showWordmark && (
        <div className="flex flex-col border-l border-border pl-3 leading-tight">
          <span className="text-sm font-semibold text-foreground">Sentinel AI</span>
          <span className="text-[11px] text-muted-foreground">Security assistant</span>
        </div>
      )}
    </div>
  );
}
