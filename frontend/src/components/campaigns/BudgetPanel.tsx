import { Coins, Gauge } from "lucide-react";
import type { CampaignBudget } from "@/lib/types";

function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

function breakdownRows(groups: Record<string, { calls: number; total_tokens: number; cost_usd: number }>) {
  return Object.entries(groups).sort((a, b) => b[1].total_tokens - a[1].total_tokens);
}

export function BudgetPanel({ budget }: { budget: CampaignBudget }) {
  const maxCost = budget.max_cost_usd ?? budget.remaining_cost_usd + budget.total_cost_usd;
  const spentPct = maxCost > 0 ? Math.min(100, (budget.total_cost_usd / maxCost) * 100) : 0;
  const roleRows = breakdownRows(budget.per_role);
  const modelRows = breakdownRows(budget.per_model);
  const maxModelTokens = Math.max(1, ...modelRows.map(([, v]) => v.total_tokens));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="card p-4">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">Tokens Used</p>
            <Gauge className="w-3.5 h-3.5 text-cyan-400" />
          </div>
          <p className="text-xl font-bold text-white mt-1">{budget.total_tokens.toLocaleString()}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">
            {budget.prompt_tokens.toLocaleString()} prompt / {budget.completion_tokens.toLocaleString()} completion
          </p>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">Cost Used</p>
            <Coins className="w-3.5 h-3.5 text-amber-400" />
          </div>
          <p className="text-xl font-bold text-amber-400 mt-1">{formatUsd(budget.total_cost_usd)}</p>
          <p className="text-[10px] text-slate-600 mt-0.5">
            {budget.max_cost_usd !== null && budget.max_cost_usd !== undefined
              ? `Budget ${formatUsd(budget.max_cost_usd)}`
              : "No cost cap set"}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Remaining</p>
          <p className="text-xl font-bold text-emerald-400 mt-1">{formatUsd(budget.remaining_cost_usd)}</p>
          <div className="mt-2 h-1 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-emerald-500 to-cyan-500 rounded-full transition-all"
              style={{ width: `${spentPct}%` }}
            />
          </div>
        </div>
      </div>

      <div className="card p-4">
        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Per Role</h4>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <th className="px-3 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase">Role</th>
                <th className="px-3 py-2 text-right text-[10px] font-semibold text-slate-500 uppercase">Calls</th>
                <th className="px-3 py-2 text-right text-[10px] font-semibold text-slate-500 uppercase">Tokens</th>
                <th className="px-3 py-2 text-right text-[10px] font-semibold text-slate-500 uppercase">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {roleRows.map(([role, v]) => (
                <tr key={role}>
                  <td className="px-3 py-2 text-xs text-slate-300 font-medium">{role}</td>
                  <td className="px-3 py-2 text-xs text-slate-400 text-right">{v.calls}</td>
                  <td className="px-3 py-2 text-xs text-slate-400 text-right">{v.total_tokens.toLocaleString()}</td>
                  <td className="px-3 py-2 text-xs text-amber-400 text-right">{formatUsd(v.cost_usd)}</td>
                </tr>
              ))}
              {roleRows.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-xs text-slate-600 text-center">No billed calls recorded yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {modelRows.length > 0 && (
        <div className="card p-4">
          <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Per Model</h4>
          <div className="space-y-3">
            {modelRows.map(([model, v]) => (
              <div key={model}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-xs text-slate-300 truncate mr-2 font-mono">{model}</span>
                  <span className="text-[10px] text-slate-500 shrink-0">
                    {v.calls} calls · {v.total_tokens.toLocaleString()} tokens · {formatUsd(v.cost_usd)}
                  </span>
                </div>
                <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-amber-500 to-cyan-500 rounded-full transition-all"
                    style={{ width: `${(v.total_tokens / maxModelTokens) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}