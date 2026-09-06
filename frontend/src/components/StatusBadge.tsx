interface StatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

const statusStyles: Record<string, string> = {
  COMPLETED: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  RUNNING: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  PENDING: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  FAILED: "bg-red-500/20 text-red-400 border-red-500/30",
  SUCCESSFUL: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  FAILED_ATTACK: "bg-red-500/20 text-red-400 border-red-500/30",
  CONFIRMED_VULNERABILITY: "bg-red-500/20 text-red-400 border-red-500/30",
  UNCONFIRMED: "bg-slate-500/20 text-slate-400 border-slate-500/30",
  FALSE_POSITIVE: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  VERIFIED: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
};

const defaultStyle = "bg-slate-500/20 text-slate-400 border-slate-500/30";

export function StatusBadge({ status, size = "sm" }: StatusBadgeProps) {
  const style = statusStyles[status] || defaultStyle;
  const sizeClasses = size === "sm" ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-1 text-xs";
  return (
    <span className={`inline-flex items-center rounded-md border font-semibold uppercase tracking-wider ${style} ${sizeClasses}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}
