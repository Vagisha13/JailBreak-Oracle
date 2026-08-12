"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, ShieldAlert, Target, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { RedTeamReport } from "@/lib/types";

export default function ReportDetailPage() {
  const params = useParams();
  const id = params.id as string;
  
  const [report, setReport] = useState<RedTeamReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const fetchReport = async () => {
      try {
        const res = await api.get<RedTeamReport>(`/reports/experiment/${id}`);
        setReport(res.data);
      } catch (err) {
        console.error(err);
        setError("Failed to load report. Ensure the experiment ID is correct.");
      } finally {
        setLoading(false);
      }
    };
    
    if (id) fetchReport();
  }, [id]);

  if (loading) return <div className="p-8 text-center text-slate-500">Compiling Report Data...</div>;
  if (error || !report) return <div className="p-8 text-center text-rose-500">{error}</div>;

  // Determine Risk Color
  const riskColor = 
    report.overall_risk_score > 75 ? "text-rose-600" :
    report.overall_risk_score > 40 ? "text-orange-500" : 
    "text-emerald-500";

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center gap-4 border-b border-slate-200 pb-6">
        <Link href="/reports" className="p-2 hover:bg-slate-200 rounded-full transition-colors text-slate-500">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-slate-900">{report.experiment_name}</h2>
          <p className="text-slate-500 text-sm mt-1">Target: {report.target_name} | Date: {new Date(report.generated_at).toLocaleString()}</p>
        </div>
      </div>

      {/* Top Metrics Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm text-center">
          <p className="text-sm font-medium text-slate-500 mb-2">Overall Risk Score</p>
          <p className={`text-6xl font-bold ${riskColor}`}>{report.overall_risk_score}</p>
          <p className="text-xs text-slate-400 mt-2">Scale: 0 (Safe) - 100 (Critical)</p>
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm text-center">
          <p className="text-sm font-medium text-slate-500 mb-2">Jailbreak Success Rate</p>
          <p className="text-6xl font-bold text-slate-800">{report.jailbreak_success_rate}%</p>
          <p className="text-xs text-slate-400 mt-2">{report.total_attacks_executed} Total Attacks</p>
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex flex-col justify-center">
          <p className="text-sm font-medium text-slate-500 mb-4 text-center">Severity Breakdown</p>
          <div className="grid grid-cols-2 gap-4 text-center">
            <div>
              <p className="text-2xl font-bold text-rose-600">{report.severity_breakdown.critical}</p>
              <p className="text-xs text-slate-500 uppercase font-semibold">Critical</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-orange-500">{report.severity_breakdown.high}</p>
              <p className="text-xs text-slate-500 uppercase font-semibold">High</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-yellow-500">{report.severity_breakdown.medium}</p>
              <p className="text-xs text-slate-500 uppercase font-semibold">Med</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-blue-500">{report.severity_breakdown.low}</p>
              <p className="text-xs text-slate-500 uppercase font-semibold">Low</p>
            </div>
          </div>
        </div>
      </div>

      {/* Strategy Performance Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-200 bg-slate-50 flex items-center gap-2">
          <Target className="w-5 h-5 text-slate-500" />
          <h3 className="font-semibold text-slate-800">Strategy Performance</h3>
        </div>
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-white">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Strategy Name</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Attempts</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Successes</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Win Rate</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 bg-white">
            {report.strategy_performance.length === 0 ? (
              <tr><td colSpan={4} className="p-4 text-center text-sm text-slate-500">No attack data available yet.</td></tr>
            ) : (
              report.strategy_performance.map((strat, idx) => (
                <tr key={idx}>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900">{strat.strategy_name}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{strat.total_attempts}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500">{strat.successful_jailbreaks}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-500 font-medium">
                    {strat.success_rate_percentage}%
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Top Vulnerabilities */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden mb-6">
        <div className="p-4 border-b border-slate-200 bg-slate-50 flex items-center gap-2">
          <ShieldAlert className="w-5 h-5 text-slate-500" />
          <h3 className="font-semibold text-slate-800">Confirmed Vulnerabilities</h3>
        </div>
        <div className="p-0">
          {report.top_vulnerabilities.length === 0 ? (
            <p className="p-6 text-center text-sm text-slate-500">No confirmed vulnerabilities found in this campaign.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {report.top_vulnerabilities.map((vuln, idx) => (
                <li key={idx} className="p-6 hover:bg-slate-50 transition-colors">
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex items-center gap-3">
                      <span className={`px-2 py-1 rounded text-xs font-bold ${
                        vuln.severity === 'CRITICAL' ? 'bg-rose-100 text-rose-800' :
                        vuln.severity === 'HIGH' ? 'bg-orange-100 text-orange-800' :
                        'bg-blue-100 text-blue-800'
                      }`}>
                        {vuln.severity}
                      </span>
                      <span className="font-medium text-slate-800">{vuln.category}</span>
                    </div>
                    <span className="text-xs text-slate-400 bg-slate-100 px-2 py-1 rounded">
                      Confidence: {Math.round(vuln.confidence * 100)}%
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 mt-2">{vuln.reasoning}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}