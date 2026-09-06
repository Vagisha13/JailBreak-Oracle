"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface CollapsibleProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  maxHeight?: string;
}

export function Collapsible({ title, children, defaultOpen = false, maxHeight = "max-h-40" }: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-slate-700/50 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-slate-800/50 hover:bg-slate-800 transition-colors text-left"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />}
        <span className="text-xs font-medium text-slate-300 truncate">{title}</span>
      </button>
      {open && (
        <div className={`px-3 py-2 text-xs text-slate-300 bg-slate-900/50 overflow-auto ${maxHeight}`}>
          {children}
        </div>
      )}
    </div>
  );
}
