"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, GitBranch } from "lucide-react";
import type { AttackDetail } from "@/lib/types";
import { StatusBadge } from "@/components/StatusBadge";
import { SeverityBadge } from "@/components/campaigns/SeverityBadge";

interface MutationTreeProps {
  attacks: AttackDetail[];
}

interface MutationTreeNodeProps {
  attack: AttackDetail;
  depth: number;
  hasChildren: boolean;
  collapsed: boolean;
  onToggle: (id: string) => void;
  children: React.ReactNode;
}

function MutationTreeNode({
  attack,
  depth,
  hasChildren,
  collapsed,
  onToggle,
  children,
}: MutationTreeNodeProps) {
  const indent = depth * 20;
  return (
    <div>
      <div
        className="flex items-center gap-2 py-2 group"
        style={{ paddingLeft: `${indent}px` }}
      >
        {hasChildren ? (
          <button
            onClick={() => onToggle(attack.id)}
            className="shrink-0 text-slate-500 hover:text-cyan-400 transition-colors"
            aria-label={collapsed ? "Expand branch" : "Collapse branch"}
          >
            {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        <span
          className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-mono font-bold shrink-0 ${
            attack.is_jailbreak
              ? "bg-red-500/20 border-2 border-red-500/50 text-red-400"
              : "bg-slate-800 border-2 border-slate-700 text-slate-400"
          }`}
        >
          {attack.round_number ?? "-"}
        </span>
        <span className="text-[10px] font-mono text-slate-400 truncate">
          {attack.strategy_name.replace(/_/g, " ")}
        </span>
        {attack.mutation_type && (
          <span className="px-1.5 py-0.5 text-[9px] font-semibold uppercase rounded bg-violet-500/20 text-violet-400 border border-violet-500/30">
            {attack.mutation_type.replace(/_/g, " ")}
          </span>
        )}
        {attack.is_jailbreak ? (
          <StatusBadge status="SUCCESSFUL" />
        ) : (
          <StatusBadge status="FAILED_ATTACK" />
        )}
        <SeverityBadge severity={attack.severity} />
      </div>
      {hasChildren && (
        <div className="ml-[6px] border-l border-slate-800">
          {children}
        </div>
      )}
    </div>
  );
}

export function MutationTree({ attacks }: MutationTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

  const childrenByParent = new Map<string, AttackDetail[]>();
  const allIds = new Set(attacks.map((a) => a.id));
  attacks.forEach((a) => {
    if (a.parent_attack_id && allIds.has(a.parent_attack_id)) {
      const siblings = childrenByParent.get(a.parent_attack_id) ?? [];
      siblings.push(a);
      childrenByParent.set(a.parent_attack_id, siblings);
    }
  });

  const roots = attacks.filter(
    (a) => !a.parent_attack_id || !allIds.has(a.parent_attack_id)
  );

  const toggle = (id: string) => {
    const next = new Set(collapsed);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setCollapsed(next);
  };

  const renderNode = (attack: AttackDetail, depth: number, visited: Set<string>) => {
    if (visited.has(attack.id)) {
      return (
        <div className="flex items-center gap-2 py-2 text-[10px] text-slate-600 font-mono">
          <span className="ml-7">cycle detected at {attack.round_number ?? "?"}</span>
        </div>
      );
    }
    const children = childrenByParent.get(attack.id) ?? [];
    const nextVisited = new Set(visited);
    nextVisited.add(attack.id);
    return (
      <MutationTreeNode
        key={attack.id}
        attack={attack}
        depth={depth}
        hasChildren={children.length > 0}
        collapsed={collapsed.has(attack.id)}
        onToggle={toggle}
      >
        {!collapsed.has(attack.id) &&
          children.map((child) => renderNode(child, depth + 1, nextVisited))}
      </MutationTreeNode>
    );
  };

  if (roots.length === 0) {
    return (
      <p className="text-xs text-slate-600">
        No lineage recorded for this campaign&apos;s attacks.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 pb-2">
        <GitBranch className="w-3.5 h-3.5 text-violet-400" />
        <span className="text-[10px] font-mono text-slate-500">Lineage</span>
      </div>
      {roots.map((root) => renderNode(root, 0, new Set<string>()))}
    </div>
  );
}