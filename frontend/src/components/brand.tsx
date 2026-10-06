import { cn } from "@/lib/utils";

export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("h-6 w-6", className)} aria-hidden>
      <rect width="32" height="32" rx="7" className="fill-raised" />
      <path d="M7 9h18v4.5H14.2V16h8.3v4.2h-8.3V23H7z" className="fill-ember" />
    </svg>
  );
}
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <Mark />
      <span className="text-[15px] font-semibold tracking-[0.14em]">FORGE</span>
    </span>
  );
}
