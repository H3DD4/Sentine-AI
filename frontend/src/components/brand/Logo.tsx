import logo from "@/assets/forvis-mazars-logo.png";
import { cn } from "@/lib/utils";

export function Logo({
  className,
  imageClassName,
  textClassName,
  showWordmark = true,
}: {
  className?: string;
  imageClassName?: string;
  textClassName?: string;
  showWordmark?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <img
        src={logo}
        alt="Forvis Mazars"
        className={cn("h-8 w-auto object-contain", imageClassName)}
      />
      {showWordmark && (
        <div className="flex flex-col border-l border-border pl-3 leading-tight">
          <span className={cn("text-sm font-semibold text-foreground", textClassName)}>
            Sentinel AI
          </span>
          <span className={cn("text-[11px] text-muted-foreground", textClassName)}>
            Security assistant
          </span>
        </div>
      )}
    </div>
  );
}
