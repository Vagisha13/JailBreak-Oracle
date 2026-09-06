"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Loader2,
  Quote,
  ScrollText,
  ShieldCheck,
} from "lucide-react";
import type { Finding, FindingDetail } from "@/lib/types";
import { getVulnerabilityDetail } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { SeverityBadge } from "@/components/campaigns/SeverityBadge";

function EvidenceList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
        <Quote className="w-3 h-3 text-cyan-400" /> {label}
      </p>
      <ul className="space-y-1">
        {items.map((item, idx) => (
          <li
            key={idx}
            className="text-xs text-slate-300 bg-slate-800/40 border border-slate-700/50 rounded-md px-2.5 py-1.5 whitespace-pre-wrap break-words"
          >
            &ldquo;{item}&rdquo;
          </li>
        ))}
      </ul>
    </div>
  );
}

function FindingDetailBody({ finding }: { finding: FindingDetail }) {
  const attack = finding.attack;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="px-3 py-2 rounded-md bg-slate-800/40 border border-slate-700/50">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Confidence</p>
          <p className="text-sm font-semibold text-white mt-0.5">{finding.confidence.toFixed(2)}</p>
        </div>
        <div className="px-3 py-2 rounded-md bg-slate-800/40 border border-slate-700/50">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Verifier Confidence</p>
          <p className="text-sm font-semibold text-white mt-0.5">
            {finding.verifier_confidence !== null && finding.verifier_confidence !== undefined
              ? finding.verifier_confidence.toFixed(2)
              : "-"}
          </p>
        </div>
        <div className="px-3 py-2 rounded-md bg-slate-800/40 border border-slate-700/50">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Verdict</p>
          <p className="mt-1"><StatusBadge status={finding.verified_status} /></p>
        </div>
        <div className="px-3 py-2 rounded-md bg-slate-800/40 border border-slate-700/50">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Identified</p>
          <p className="text-xs text-slate-300 mt-1">
            {finding.created_at ? new Date(finding.created_at).toLocaleString() : "-"}
          </p>
        </div>
      </div>

      <div>
        <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
          <ScrollText className="w-3 h-3 text-slate-400" /> Evaluator Reasoning
        </p>
        <p className="text-xs text-slate-300 bg-slate-800/40 border border-slate-700/50 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words">
          {finding.reasoning || "No reasoning recorded."}
        </p>
      </div>

      {finding.verification_reasoning && (
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
            <ShieldCheck className="w-3 h-3 text-emerald-400" /> Verifier Reasoning
          </p>
          <p className="text-xs text-slate-300 bg-slate-800/40 border border-slate-700/50 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words">
            {finding.verification_reasoning}
          </p>
        </div>
      )}

      {finding.remediation_guidance && (
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Remediation Guidance</p>
          <p className="text-xs text-emerald-300/90 bg-emerald-500/[0.06] border border-emerald-500/20 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words">
            {finding.remediation_guidance}
          </p>
        </div>
      )}

      <EvidenceList label="Evaluator Evidence" items={finding.evaluator_evidence} />
      <EvidenceList label="Verifier Evidence" items={finding.verifier_evidence} />

      {attack && (
        <div className="space-y-2">
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
            Attacking Prompt
          </p>
          <p className="text-xs text-slate-300 bg-slate-800/40 border border-slate-700/50 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words">
            {attack.prompt_text}
          </p>
          <div className="flex items-center gap-2 flex-wrap text-[10px] text-slate-500 font-mono">
            <span>{attack.strategy_name.replace(/_/g, " ")}</span>
            <span>·</span>
            <span>Round {attack.round_number ?? "-"}</span>
            {attack.parent_attack_id && <span>· mutated from {attack.parent_attack_id.slice(0, 8)}</span>}
            {attack.latency_ms != null && <span>· {Math.round(attack.latency_ms)}ms</span>}
          </div>
          {attack.target_response && (
            <>
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider pt-1">
                Target Response
              </p>
              <p className="text-xs text-slate-300 bg-slate-800/40 border border-slate-700/50 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words">
                {attack.target_response}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function FindingRow({
  finding,
  detail,
  loading,
  onToggle,
}: {
  finding: Finding;
  detail: FindingDetail | null;
  loading: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="card p-4">
      <button
        onClick={() => onToggle(finding.id)}
        className="w-full flex items-center gap-3 text-left"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <SeverityBadge severity={finding.severity} />
            <StatusBadge status={finding.verified_status} />
            <span className="text-[10px] font-mono text-slate-500 uppercase">{finding.category}</span>
          </div>
          <p className="text-xs text-slate-400 mt-1.5 truncate">
            {finding.reasoning}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <span className="text-[10px] text-slate-500 font-mono hidden sm:inline">
            {finding.created_at ? new Date(finding.created_at).toLocaleString() : ""}
          </span>
          {loading ? (
            <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
          ) : detail ? (
            <ChevronDown className="w-3.5 h-3.5 text-slate-500" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-slate-500" />
          )}
        </div>
      </button>
      {detail && (
        <div className="mt-4 pt-4 border-t border-slate-800">
          <FindingDetailBody finding={detail} />
        </div>
      )}
    </div>
  );
}

export function FindingsView({ findings }: { findings: Finding[] }) {
  const [details, setDetails] = useState<Record<string, FindingDetail>>({});
  const [loadingIds, setLoadingIds] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const toggle = async (id: string) => {
    if (expanded.has(id)) {
      const next = new Set(expanded);
      next.delete(id);
      setExpanded(next);
      return;
    }
    const next = new Set(expanded);
    next.add(id);
    setExpanded(next);
    if (!details[id]) {
      const nextLoading = new Set(loadingIds);
      nextLoading.add(id);
      setLoadingIds(nextLoading);
      try {
        const res = await getVulnerabilityDetail(id);
        setDetails((prev) => ({ ...prev, [id]: res.data }));
      } catch {
        // leave detail unresolved; the opened row shows nothing extra.
      } finally {
        setLoadingIds((prev) => {
          const nextSet = new Set(prev);
          nextSet.delete(id);
          return nextSet;
        });
      }
    }
  };

  if (findings.length === 0) {
    return (
      <div className="card p-8 text-center">
        <div className="mx-auto w-fit p-3 rounded-full bg-slate-800 border border-slate-700 mb-3">
          <ClipboardList className="w-6 h-6 text-slate-500" />
        </div>
        <p className="text-sm text-slate-400">No confirmed findings recorded for this campaign.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {findings.map((finding) => (
        <FindingRow
          key={finding.id}
          finding={finding}
          detail={details[finding.id] ?? null}
          loading={loadingIds.has(finding.id)}
          onToggle={toggle}
        />
      ))}
    </div>
  );
}