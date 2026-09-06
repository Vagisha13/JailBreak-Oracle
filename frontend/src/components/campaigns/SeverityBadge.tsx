interface SeverityBadgeProps {
  severity: string | null;
}

const severityStyles: Record<string, string> = {
  CRITICAL: "bg-red-500/20 text-red-400 border-red-500/30",
  HIGH: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  MEDIUM: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  LOW: "bg-blue-500/20 text-blue-400 border-blue-500/30",
};

const defaultStyle = "bg-slate-500/20 text-slate-400 border-slate-500/30";

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  const normalized = severity ? severity.toUpperCase() : "";
  return (
    <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${severityStyles[normalized] || defaultStyle}`}>
      {severity || "UNRATED"}
    </span>
  );
}