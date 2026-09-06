"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft, Loader2, Clock, Target,
  Activity, Zap, CheckCircle2,
  XCircle, BarChart3,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { Collapsible } from "@/components/Collapsible";
import type { CampaignStatus, AttackDetail } from "@/lib/types";

interface Analytics {
  totalAttacks: number;
  successfulAttacks: number;
  verifiedAttacks: number;
  successRate: number;
  byStrategy: Record<string, { total: number; successful: number; rate: number }>;
  byIteration: Record<string, { total: number; successful: number }>;
  avgLatency: number;
}

function computeAnalytics(attacks: AttackDetail[]): Analytics {
  const byStrategy: Record<string, { total: number; successful: number; rate: number }> = {};
  const byIteration: Record<string, { total: number; successful: number }> = {};
  let totalLatency = 0;
  let latencyCount = 0;

  attacks.forEach((a) => {
    if (!byStrategy[a.strategy_name]) {
      byStrategy[a.strategy_name] = { total: 0, successful: 0, rate: 0 };
    }
    byStrategy[a.strategy_name].total++;
    if (a.is_jailbreak) byStrategy[a.strategy_name].successful++;

    const iterKey = `Attack ${attacks.indexOf(a) + 1}`;
    if (!byIteration[iterKey]) byIteration[iterKey] = { total: 0, successful: 0 };
    byIteration[iterKey].total++;
    if (a.is_jailbreak) byIteration[iterKey].successful++;

    if (a.latency_ms) {
      totalLatency += a.latency_ms;
      latencyCount++;
    }
  });

  Object.values(byStrategy).forEach((s) => {
    s.rate = s.total > 0 ? (s.successful / s.total) * 100 : 0;
  });

  const totalAttacks = attacks.length;
  const successfulAttacks = attacks.filter((a) => a.is_jailbreak).length;
  const verifiedAttacks = attacks.filter((a) => a.verified_status === "CONFIRMED_VULNERABILITY").length;

  return {
    totalAttacks,
    successfulAttacks,
    verifiedAttacks,
    successRate: totalAttacks > 0 ? (successfulAttacks / totalAttacks) * 100 : 0,
    byStrategy,
    byIteration,
    avgLatency: latencyCount > 0 ? totalLatency / latencyCount : 0,
  };
}

function TimelineView({ attacks }: { attacks: AttackDetail[] }) {
  return (
    <div className="space-y-0">
      {attacks.map((attack, idx) => (
        <div key={attack.id} className="relative flex gap-4">
          <div className="flex flex-col items-center">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                attack.is_jailbreak
                  ? "bg-red-500/20 border-2 border-red-500/50 text-red-400"
                  : "bg-slate-800 border-2 border-slate-700 text-slate-400"
              }`}
            >
              {idx + 1}
            </div>
            {idx < attacks.length - 1 && (
              <div className="w-0.5 flex-1 bg-slate-800 min-h-[2rem]" />
            )}
          </div>
          <div className="pb-6 flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <span className="text-xs font-mono text-slate-400">
                {attack.strategy_name.replace(/_/g, " ")}
              </span>
              <StatusBadge
                status={attack.is_jailbreak ? "SUCCESSFUL" : "FAILED"}
              />
              {attack.verified_status && (
                <StatusBadge status={attack.verified_status === "CONFIRMED_VULNERABILITY" ? "VERIFIED" : attack.verified_status} />
              )}
              {attack.severity && (
                <span className={`px-1.5 py-0.5 text-[9px] font-semibold uppercase rounded ${
                  attack.severity === "CRITICAL"
                    ? "bg-red-500/20 text-red-400"
                    : attack.severity === "HIGH"
                    ? "bg-orange-500/20 text-orange-400"
                    : attack.severity === "MEDIUM"
                    ? "bg-amber-500/20 text-amber-400"
                    : "bg-blue-500/20 text-blue-400"
                }`}>
                  {attack.severity}
                </span>
              )}
            </div>
            <Collapsible title={`Prompt: ${attack.prompt_text.substring(0, 80)}...`}>
              <p className="whitespace-pre-wrap break-words">{attack.prompt_text}</p>
            </Collapsible>
            {attack.target_response && (
              <div className="mt-2">
                <Collapsible title={`Response: ${attack.target_response.substring(0, 80)}...`}>
                  <p className="whitespace-pre-wrap break-words text-slate-300">{attack.target_response}</p>
                </Collapsible>
              </div>
            )}
            {attack.latency_ms && (
              <p className="text-[10px] text-slate-600 mt-1">
                Latency: {Math.round(attack.latency_ms)}ms
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function AnalyticsPanel({ analytics }: { analytics: Analytics }) {
  const strategyEntries = Object.entries(analytics.byStrategy).sort(
    (a, b) => b[1].rate - a[1].rate
  );

  const maxTotal = Math.max(1, ...strategyEntries.map(([, v]) => v.total));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-white">{analytics.totalAttacks}</p>
          <p className="text-[10px] text-slate-500 uppercase mt-1">Total Attacks</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-red-400">{analytics.successfulAttacks}</p>
          <p className="text-[10px] text-slate-500 uppercase mt-1">Jailbreaks</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-amber-400">{analytics.verifiedAttacks}</p>
          <p className="text-[10px] text-slate-500 uppercase mt-1">Verified</p>
        </div>
        <div className="card p-4 text-center">
          <p className="text-2xl font-bold text-cyan-400">{analytics.successRate.toFixed(1)}%</p>
          <p className="text-[10px] text-slate-500 uppercase mt-1">Success Rate</p>
        </div>
      </div>

      {strategyEntries.length > 0 && (
        <div className="card p-4">
          <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
            <BarChart3 className="w-3.5 h-3.5" /> Success by Strategy
          </h4>
          <div className="space-y-2.5">
            {strategyEntries.map(([name, data]) => (
              <div key={name}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-xs text-slate-300 truncate mr-2">
                    {name.replace(/_/g, " ")}
                  </span>
                  <span className="text-[10px] text-slate-500 shrink-0">
                    {data.successful}/{data.total} ({data.rate.toFixed(0)}%)
                  </span>
                </div>
                <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-cyan-500 to-violet-500 rounded-full transition-all"
                    style={{ width: `${(data.total / maxTotal) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {analytics.avgLatency > 0 && (
        <div className="card p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 flex items-center gap-2">
              <Clock className="w-3.5 h-3.5" /> Average Latency
            </span>
            <span className="text-sm font-semibold text-white">
              {Math.round(analytics.avgLatency)}ms
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CampaignDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { isAuthenticated, loading: authLoading } = useAuth();
  const id = params.id as string;

  const [status, setStatus] = useState<CampaignStatus | null>(null);
  const [attacks, setAttacks] = useState<AttackDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"overview" | "attacks" | "analytics" | "timeline">("overview");

  useEffect(() => {
    if (!authLoading && !isAuthenticated) router.push("/login");
  }, [authLoading, isAuthenticated, router]);

  const fetchData = useCallback(async () => {
    try {
      const [statusRes, attacksRes] = await Promise.all([
        api.get<CampaignStatus>(`/campaigns/${id}/status`),
        api.get<AttackDetail[]>(`/campaigns/${id}/attacks`),
      ]);
      setStatus(statusRes.data);
      setAttacks(attacksRes.data);
      setError("");
    } catch {
      setError("Failed to load campaign data. It may not exist or the backend is unreachable.");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (!isAuthenticated) return;
    let active = true;
    const run = async () => {
      try {
        const [statusRes, attacksRes] = await Promise.all([
          api.get<CampaignStatus>(`/campaigns/${id}/status`),
          api.get<AttackDetail[]>(`/campaigns/${id}/attacks`),
        ]);
        if (active) {
          setStatus(statusRes.data);
          setAttacks(attacksRes.data);
          setError("");
        }
      } catch {
        if (active) {
          setError("Failed to load campaign data. It may not exist or the backend is unreachable.");
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    run();
    return () => {
      active = false;
    };
  }, [isAuthenticated, id]);

  useEffect(() => {
    if (!isAuthenticated || !status) return;
    if (status.status === "RUNNING" || status.status === "PENDING") {
      const interval = setInterval(fetchData, 3000);
      return () => clearInterval(interval);
    }
  }, [isAuthenticated, status, fetchData]);

  if (authLoading || !isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-4">
        <Loader2 className="w-8 h-8 text-cyan-400 animate-spin" />
        <p className="text-sm text-slate-400">Loading campaign data...</p>
      </div>
    );
  }

  if (error || !status) {
    return <ErrorState message={error || "Campaign not found."} onRetry={fetchData} />;
  }

  const analytics = computeAnalytics(attacks);
  const isRunning = status.status === "RUNNING" || status.status === "PENDING";

  const tabs = [
    { key: "overview" as const, label: "Overview", icon: Activity },
    { key: "attacks" as const, label: `Attacks (${attacks.length})`, icon: Zap },
    { key: "analytics" as const, label: "Analytics", icon: BarChart3 },
    { key: "timeline" as const, label: "Timeline", icon: Clock },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-4">
        <Link href="/campaigns" className="btn-ghost px-2 mt-1">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-2xl font-bold tracking-tight text-white truncate">{status.name}</h2>
            <StatusBadge status={status.status} size="md" />
            {isRunning && (
              <span className="flex items-center gap-1.5 text-xs text-cyan-400">
                <span className="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-pulse" />
                Live
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-1 font-mono">{status.experiment_id}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card p-4">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Attacks</p>
          <p className="text-xl font-bold text-white mt-1">{analytics.totalAttacks}/{status.attack_budget}</p>
          <div className="mt-2 h-1 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-cyan-500 rounded-full transition-all"
              style={{ width: `${Math.min(100, (analytics.totalAttacks / status.attack_budget) * 100)}%` }}
            />
          </div>
        </div>
        <div className="card p-4">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Jailbreaks</p>
          <p className="text-xl font-bold text-red-400 mt-1">{analytics.successfulAttacks}</p>
        </div>
        <div className="card p-4">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Success Rate</p>
          <p className="text-xl font-bold text-cyan-400 mt-1">{analytics.successRate.toFixed(1)}%</p>
        </div>
        <div className="card p-4">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider">Started</p>
          <p className="text-sm font-medium text-slate-300 mt-1">
            {status.created_at ? new Date(status.created_at).toLocaleString() : "-"}
          </p>
          {status.finished_at && (
            <p className="text-[10px] text-slate-500 mt-0.5">
              Finished: {new Date(status.finished_at).toLocaleString()}
            </p>
          )}
        </div>
      </div>

      <div className="flex gap-1 p-1 bg-slate-900 rounded-lg border border-slate-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-md transition-all flex-1 justify-center ${
              tab === t.key
                ? "bg-slate-800 text-cyan-400 shadow-sm"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">{t.label}</span>
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="space-y-4">
          {attacks.length === 0 ? (
            <EmptyState
              message={isRunning ? "Campaign is running. Attacks will appear shortly." : "No attacks were executed in this campaign."}
            />
          ) : (
            <>
              <AnalyticsPanel analytics={analytics} />
              <div className="card p-4">
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                  <Target className="w-3.5 h-3.5" /> Strategy Breakdown
                </h4>
                <div className="overflow-x-auto">
                  <table className="min-w-full">
                    <thead>
                      <tr className="border-b border-slate-800">
                        <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase">Strategy</th>
                        <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase">Attempts</th>
                        <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase">Successes</th>
                        <th className="px-4 py-2 text-left text-[10px] font-semibold text-slate-500 uppercase">Rate</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50">
                      {Object.entries(analytics.byStrategy)
                        .sort((a, b) => b[1].total - a[1].total)
                        .map(([name, data]) => (
                          <tr key={name}>
                            <td className="px-4 py-2.5 text-xs text-slate-300 font-medium">{name.replace(/_/g, " ")}</td>
                            <td className="px-4 py-2.5 text-xs text-slate-400">{data.total}</td>
                            <td className="px-4 py-2.5 text-xs text-red-400">{data.successful}</td>
                            <td className="px-4 py-2.5 text-xs text-cyan-400 font-medium">{data.rate.toFixed(1)}%</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "attacks" && (
        <div>
          {attacks.length === 0 ? (
            <EmptyState
              message={isRunning ? "No attacks yet. The campaign is still running." : "No attacks were recorded."}
            />
          ) : (
            <div className="space-y-2">
              {attacks.map((attack) => (
                <div
                  key={attack.id}
                  className={`card p-4 transition-colors ${
                    attack.is_jailbreak ? "border-red-500/20 bg-red-500/[0.02]" : ""
                  }`}
                >
                  <div className="flex items-center gap-2 mb-3 flex-wrap">
                    <span className="text-[10px] font-mono text-slate-500">
                      {attack.created_at ? new Date(attack.created_at).toLocaleTimeString() : ""}
                    </span>
                    <span className="text-[10px] font-mono text-slate-400">
                      {attack.strategy_name.replace(/_/g, " ")}
                    </span>
                    <span className="text-[10px] font-mono text-slate-600">
                      {attack.category}
                    </span>
                    {attack.is_jailbreak ? (
                      <span className="flex items-center gap-1 text-[10px] text-red-400 font-semibold">
                        <CheckCircle2 className="w-3 h-3" /> Jailbreak
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[10px] text-slate-500">
                        <XCircle className="w-3 h-3" /> Failed
                      </span>
                    )}
                    {attack.severity && (
                      <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase rounded ${
                        attack.severity === "CRITICAL"
                          ? "bg-red-500/20 text-red-400"
                          : attack.severity === "HIGH"
                          ? "bg-orange-500/20 text-orange-400"
                          : attack.severity === "MEDIUM"
                          ? "bg-amber-500/20 text-amber-400"
                          : "bg-blue-500/20 text-blue-400"
                      }`}>
                        {attack.severity}
                      </span>
                    )}
                    {attack.verified_status && (
                      <StatusBadge status={attack.verified_status === "CONFIRMED_VULNERABILITY" ? "VERIFIED" : attack.verified_status === "FALSE_POSITIVE" ? "FALSE_POSITIVE" : "UNCONFIRMED"} />
                    )}
                    {attack.latency_ms != null && (
                      <span className="text-[10px] text-slate-600 ml-auto">{Math.round(attack.latency_ms)}ms</span>
                    )}
                  </div>

                  <div className="space-y-2">
                    <Collapsible title={`Attack Prompt (${attack.prompt_text.length} chars)`} defaultOpen={false}>
                      <p className="whitespace-pre-wrap break-words leading-relaxed">{attack.prompt_text}</p>
                    </Collapsible>
                    {attack.target_response && (
                      <Collapsible title={`Target Response (${attack.target_response.length} chars)`} defaultOpen={attack.is_jailbreak}>
                        <p className="whitespace-pre-wrap break-words leading-relaxed text-slate-300">{attack.target_response}</p>
                      </Collapsible>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "analytics" && <AnalyticsPanel analytics={analytics} />}

      {tab === "timeline" && (
        <div>
          {attacks.length === 0 ? (
            <EmptyState
              message={isRunning ? "Waiting for attack data..." : "No attacks to display in timeline."}
            />
          ) : (
            <div className="card p-5">
              <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                <Zap className="w-3.5 h-3.5" /> Attack Sequence
              </h4>
              <TimelineView attacks={attacks} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
