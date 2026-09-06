"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ShieldAlert, Target, Loader2, ShieldCheck, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ErrorState } from "@/components/ErrorState";
import { RedTeamReport } from "@/lib/types";

export default function ReportDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { isAuthenticated, loading: authLoading } = useAuth();
  const id = params.id as string;

  const [report, setReport] = useState<RedTeamReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!authLoading && !isAuthenticated) router.push("/login");
  }, [authLoading, isAuthenticated, router]);

  useEffect(() => {
    if (!isAuthenticated) return;
    const fetchReport = async () => {
      try {
        const res = await api.get<RedTeamReport>(`/reports/experiment/${id}`);
        setReport(res.data);
      } catch {
        setError("Failed to load report. The campaign may not have a report yet.");
      } finally {
        setLoading(false);
      }
    };
    if (id) fetchReport();
  }, [id, isAuthenticated]);

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
        <p className="text-sm text-slate-400">Compiling report data...</p>
      </div>
    );
  }

  if (error || !report) return <ErrorState message={error || "Report not found."} onRetry={() => router.push("/reports")} />;

  const riskColor =
    report.overall_risk_score > 75 ? "text-red-400" :
    report.overall_risk_score > 40 ? "text-orange-400" :
    "text-emerald-400";

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      <div className="flex items-start gap-4 border-b border-slate-800 pb-6">
        <Link href="/reports" className="btn-ghost px-2 mt-1">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white">{report.experiment_name}</h2>
          <p className="text-slate-500 text-sm mt-1">
            Target: <span className="text-cyan-400">{report.target_name}</span> |{" "}
            Generated: {new Date(report.generated_at).toLocaleString()}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card p-6 text-center">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">Overall Risk Score</p>
          <p className={`text-6xl font-bold ${riskColor}`}>{report.overall_risk_score}</p>
          <p className="text-[10px] text-slate-500 mt-2">Scale: 0 (Safe) - 100 (Critical)</p>
        </div>

        <div className="card p-6 text-center">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">Jailbreak Success Rate</p>
          <p className="text-6xl font-bold text-white">{report.jailbreak_success_rate}%</p>
          <p className="text-[10px] text-slate-500 mt-2">{report.total_attacks_executed} Total Attacks</p>
        </div>

        <div className="card p-6 flex flex-col justify-center">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-4 text-center">Severity Breakdown</p>
          <div className="grid grid-cols-2 gap-3 text-center">
            <div className="p-2 rounded-lg bg-red-500/10 border border-red-500/20">
              <p className="text-2xl font-bold text-red-400">{report.severity_breakdown.critical}</p>
              <p className="text-[10px] text-slate-500 uppercase font-semibold mt-0.5">Critical</p>
            </div>
            <div className="p-2 rounded-lg bg-orange-500/10 border border-orange-500/20">
              <p className="text-2xl font-bold text-orange-400">{report.severity_breakdown.high}</p>
              <p className="text-[10px] text-slate-500 uppercase font-semibold mt-0.5">High</p>
            </div>
            <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20">
              <p className="text-2xl font-bold text-amber-400">{report.severity_breakdown.medium}</p>
              <p className="text-[10px] text-slate-500 uppercase font-semibold mt-0.5">Medium</p>
            </div>
            <div className="p-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
              <p className="text-2xl font-bold text-blue-400">{report.severity_breakdown.low}</p>
              <p className="text-[10px] text-slate-500 uppercase font-semibold mt-0.5">Low</p>
            </div>
          </div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
          <Target className="w-4 h-4 text-cyan-400" />
          <h3 className="font-semibold text-slate-200 text-sm uppercase tracking-wider">Strategy Performance</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <th className="px-5 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Strategy</th>
                <th className="px-5 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Attempts</th>
                <th className="px-5 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Jailbreaks</th>
                <th className="px-5 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Win Rate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {report.strategy_performance.length === 0 ? (
                <tr>
                  <td colSpan={4} className="p-6 text-center text-sm text-slate-500">No attack data available.</td>
                </tr>
              ) : (
                report.strategy_performance.map((strat, idx) => (
                  <tr key={idx} className="hover:bg-slate-800/20 transition-colors">
                    <td className="px-5 py-3.5 text-sm font-medium text-slate-200">{strat.strategy_name.replace(/_/g, " ")}</td>
                    <td className="px-5 py-3.5 text-sm text-slate-400">{strat.total_attempts}</td>
                    <td className="px-5 py-3.5 text-sm text-red-400">{strat.successful_jailbreaks}</td>
                    <td className="px-5 py-3.5 text-sm text-cyan-400 font-medium">{strat.success_rate_percentage}%</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-red-400" />
          <h3 className="font-semibold text-slate-200 text-sm uppercase tracking-wider">Vulnerabilities</h3>
        </div>
        <div>
          {report.top_vulnerabilities.length === 0 ? (
            <p className="p-6 text-center text-sm text-slate-500">No vulnerabilities found in this campaign.</p>
          ) : (
            <ul className="divide-y divide-slate-800/50">
              {report.top_vulnerabilities.map((vuln, idx) => (
                <li key={idx} className="p-5 hover:bg-slate-800/20 transition-colors">
                  <div className="flex justify-between items-start mb-2 gap-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 text-[10px] font-bold uppercase rounded ${
                        vuln.severity === "CRITICAL" ? "bg-red-500/20 text-red-400" :
                        vuln.severity === "HIGH" ? "bg-orange-500/20 text-orange-400" :
                        vuln.severity === "MEDIUM" ? "bg-amber-500/20 text-amber-400" :
                        "bg-blue-500/20 text-blue-400"
                      }`}>
                        {vuln.severity}
                      </span>
                      <span className="text-sm font-medium text-slate-200">{vuln.category}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {vuln.verified_status === "CONFIRMED_VULNERABILITY" ? (
                        <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-semibold">
                          <ShieldCheck className="w-3 h-3" /> Verified
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-[10px] text-amber-400 font-semibold">
                          <AlertTriangle className="w-3 h-3" /> {vuln.verified_status?.replace(/_/g, " ") || "Unverified"}
                        </span>
                      )}
                      <span className="text-[10px] text-slate-500 bg-slate-800 px-2 py-1 rounded">
                        Confidence: {Math.round(vuln.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mt-2 leading-relaxed">{vuln.reasoning}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {report.remediation_summary.length > 0 && (
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <h3 className="font-semibold text-slate-200 text-sm uppercase tracking-wider">Remediation</h3>
          </div>
          <ul className="space-y-2">
            {report.remediation_summary.map((item, idx) => (
              <li key={idx} className="text-sm text-slate-300 flex items-start gap-2">
                <span className="text-cyan-400 mt-0.5 shrink-0">▸</span>
                <span className="leading-relaxed">{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
