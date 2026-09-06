"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ShieldAlert, Activity, FileText, ArrowRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import type { DashboardStats, Campaign } from "@/lib/types";

export default function Home() {
  const router = useRouter();
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentCampaigns, setRecentCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) {
      router.push("/login");
    }
  }, [authLoading, isAuthenticated, router]);

  useEffect(() => {
    if (!isAuthenticated) return;
    const fetchDashboardData = async () => {
      try {
        const [statsRes, campaignsRes] = await Promise.all([
          api.get<DashboardStats>("/campaigns/stats"),
          api.get<Campaign[]>("/campaigns"),
        ]);
        setStats(statsRes.data);
        setRecentCampaigns(campaignsRes.data.slice(0, 5));
      } catch (error) {
        console.error("Failed to fetch dashboard data:", error);
      } finally {
        setLoading(false);
      }
    };
    fetchDashboardData();
  }, [isAuthenticated]);

  if (authLoading || !isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-white">Dashboard</h2>
        <p className="text-slate-400 mt-1 text-sm">
          Oracle AI Red Teaming command center.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card p-5 flex items-start gap-4">
          <div className="p-2.5 bg-cyan-500/10 border border-cyan-500/20 rounded-lg">
            <Activity className="w-5 h-5 text-cyan-400" />
          </div>
          <div>
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">Active Campaigns</p>
            <p className="text-3xl font-bold text-white mt-1">
              {loading ? "-" : stats?.active_campaigns || 0}
            </p>
          </div>
        </div>

        <div className="card p-5 flex items-start gap-4">
          <div className="p-2.5 bg-red-500/10 border border-red-500/20 rounded-lg">
            <ShieldAlert className="w-5 h-5 text-red-400" />
          </div>
          <div>
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">Vulnerabilities Found</p>
            <p className="text-3xl font-bold text-white mt-1">
              {loading ? "-" : stats?.total_vulnerabilities || 0}
            </p>
          </div>
        </div>

        <div className="card p-5 flex items-start gap-4">
          <div className="p-2.5 bg-violet-500/10 border border-violet-500/20 rounded-lg">
            <FileText className="w-5 h-5 text-violet-400" />
          </div>
          <div>
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">Completed Reports</p>
            <p className="text-3xl font-bold text-white mt-1">
              {loading ? "-" : stats?.completed_reports || 0}
            </p>
          </div>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-800 flex justify-between items-center">
          <h3 className="text-sm font-semibold text-slate-200 uppercase tracking-wider">Recent Campaigns</h3>
          <Link
            href="/campaigns"
            className="text-xs text-cyan-400 hover:text-cyan-300 font-medium flex items-center gap-1 transition-colors"
          >
            View All <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
        <div className="divide-y divide-slate-800/50">
          {loading ? (
            <div className="p-8 flex justify-center">
              <Loader2 className="w-5 h-5 text-cyan-400 animate-spin" />
            </div>
          ) : recentCampaigns.length === 0 ? (
            <div className="p-8 text-center">
              <p className="text-sm text-slate-500">No campaigns yet. Start your first red teaming assessment.</p>
              <Link href="/campaigns/new" className="btn-primary mt-4 inline-flex">
                Create Campaign
              </Link>
            </div>
          ) : (
            recentCampaigns.map((camp) => (
              <Link
                key={camp.id}
                href={`/campaigns/${camp.id}`}
                className="px-6 py-3.5 flex justify-between items-center hover:bg-slate-800/30 transition-colors"
              >
                <div className="min-w-0">
                  <h4 className="text-sm font-medium text-slate-200 truncate">{camp.name}</h4>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {camp.created_at ? new Date(camp.created_at).toLocaleString() : "Unknown date"}
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0 ml-4">
                  <StatusBadge status={camp.status} />
                </div>
              </Link>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
