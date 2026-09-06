"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, RefreshCcw, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import type { Campaign } from "@/lib/types";

export default function CampaignsPage() {
  const router = useRouter();
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!authLoading && !isAuthenticated) router.push("/login");
  }, [authLoading, isAuthenticated, router]);

  const fetchCampaigns = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.get<Campaign[]>("/campaigns");
      setCampaigns(res.data);
    } catch {
      setError("Failed to load campaigns. Backend may be unreachable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isAuthenticated) return;
    let active = true;
    const run = async () => {
      setLoading(true);
      setError("");
      try {
        const res = await api.get<Campaign[]>("/campaigns");
        if (active) setCampaigns(res.data);
      } catch {
        if (active) setError("Failed to load campaigns. Backend may be unreachable.");
      } finally {
        if (active) setLoading(false);
      }
    };
    run();
    return () => {
      active = false;
    };
  }, [isAuthenticated]);

  if (authLoading || !isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-white">Campaigns</h2>
          <p className="text-slate-400 mt-1 text-sm">
            Manage and monitor automated red teaming experiments.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={fetchCampaigns} className="btn-secondary">
            <RefreshCcw className="w-3.5 h-3.5" /> Refresh
          </button>
          <Link href="/campaigns/new" className="btn-primary">
            <Plus className="w-3.5 h-3.5" /> New Campaign
          </Link>
        </div>
      </div>

      {error && <ErrorState message={error} onRetry={fetchCampaigns} />}

      {!error && (
        <div className="card overflow-hidden">
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <th className="px-6 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Name</th>
                <th className="px-6 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Status</th>
                <th className="px-6 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Budget</th>
                <th className="px-6 py-3 text-left text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Created</th>
                <th className="px-6 py-3 text-right text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center">
                    <Loader2 className="w-5 h-5 text-cyan-400 animate-spin mx-auto" />
                  </td>
                </tr>
              ) : campaigns.length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    <EmptyState
                      message="No campaigns found. Create one to start red teaming."
                      action={
                        <Link href="/campaigns/new" className="btn-primary">
                          <Plus className="w-3.5 h-3.5" /> New Campaign
                        </Link>
                      }
                    />
                  </td>
                </tr>
              ) : (
                campaigns.map((camp) => (
                  <tr key={camp.id} className="hover:bg-slate-800/20 transition-colors">
                    <td className="px-6 py-3.5">
                      <Link href={`/campaigns/${camp.id}`} className="text-sm font-medium text-slate-200 hover:text-cyan-400 transition-colors">
                        {camp.name}
                      </Link>
                    </td>
                    <td className="px-6 py-3.5">
                      <StatusBadge status={camp.status} />
                    </td>
                    <td className="px-6 py-3.5 text-sm text-slate-400">
                      {camp.attack_budget}
                    </td>
                    <td className="px-6 py-3.5 text-sm text-slate-500">
                      {camp.created_at ? new Date(camp.created_at).toLocaleString() : "-"}
                    </td>
                    <td className="px-6 py-3.5 text-right">
                      <Link
                        href={`/campaigns/${camp.id}`}
                        className="text-xs text-cyan-400 hover:text-cyan-300 font-medium transition-colors"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
