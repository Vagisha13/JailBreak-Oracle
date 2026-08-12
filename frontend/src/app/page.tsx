"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ShieldAlert, Activity, FileText, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { DashboardStats, Campaign } from "@/lib/types";

export default function Home() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentCampaigns, setRecentCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchDashboardData = async () => {
      try {
        const [statsRes, campaignsRes] = await Promise.all([
          api.get<DashboardStats>("/campaigns/stats"),
          api.get<Campaign[]>("/campaigns")
        ]);
        setStats(statsRes.data);
        setRecentCampaigns(campaignsRes.data.slice(0, 3)); // Only show top 3
      } catch (error) {
        console.error("Failed to fetch dashboard data:", error);
      } finally {
        setLoading(false);
      }
    };

    fetchDashboardData();
  }, []);

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-slate-900">Dashboard</h2>
        <p className="text-slate-500 mt-2">Welcome to the Oracle AI Security Red Teaming orchestrator.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex items-start gap-4">
          <div className="p-3 bg-blue-100 text-blue-600 rounded-lg">
            <Activity className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">Active Campaigns</p>
            <p className="text-3xl font-bold text-slate-900">
              {loading ? "-" : stats?.active_campaigns || 0}
            </p>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex items-start gap-4">
          <div className="p-3 bg-rose-100 text-rose-600 rounded-lg">
            <ShieldAlert className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">Total Vulnerabilities Discovered</p>
            <p className="text-3xl font-bold text-slate-900">
              {loading ? "-" : stats?.total_vulnerabilities || 0}
            </p>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex items-start gap-4">
          <div className="p-3 bg-emerald-100 text-emerald-600 rounded-lg">
            <FileText className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">Generated Reports</p>
            <p className="text-3xl font-bold text-slate-900">
              {loading ? "-" : stats?.completed_reports || 0}
            </p>
          </div>
        </div>
      </div>

      {/* Quick Access Section */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 bg-slate-50 flex justify-between items-center">
          <h3 className="font-semibold text-slate-800">Recent Campaigns</h3>
          <Link href="/campaigns" className="text-sm text-blue-600 hover:text-blue-800 font-medium flex items-center gap-1">
            View All <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
        <div className="divide-y divide-slate-100">
          {loading ? (
            <p className="p-6 text-sm text-slate-500 text-center">Loading recent activity...</p>
          ) : recentCampaigns.length === 0 ? (
            <p className="p-6 text-sm text-slate-500 text-center">No campaigns found. Start your first red teaming assessment!</p>
          ) : (
            recentCampaigns.map((camp) => (
              <div key={camp.id} className="p-4 px-6 flex justify-between items-center hover:bg-slate-50 transition-colors">
                <div>
                  <h4 className="font-medium text-slate-900">{camp.name}</h4>
                  <p className="text-xs text-slate-500 mt-1">{new Date(camp.created_at).toLocaleString()}</p>
                </div>
                <div className="flex items-center gap-4">
                  <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                    camp.status === 'COMPLETED' ? 'bg-emerald-100 text-emerald-800' :
                    camp.status === 'RUNNING' ? 'bg-blue-100 text-blue-800' :
                    'bg-slate-100 text-slate-800'
                  }`}>
                    {camp.status}
                  </span>
                  {camp.status === 'COMPLETED' && (
                    <Link href={`/reports/${camp.id}`} className="text-sm text-slate-500 hover:text-slate-900 font-medium">
                      Report
                    </Link>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}