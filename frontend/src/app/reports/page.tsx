"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FileBarChart, RefreshCcw, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { StatusBadge } from "@/components/StatusBadge";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import type { Campaign } from "@/lib/types";

export default function ReportsListPage() {
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
      const completed = res.data.filter((c) => c.status === "COMPLETED");
      setCampaigns(completed);
    } catch {
      setError("Failed to load reports. Backend may be unreachable.");
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
        if (active) {
          setCampaigns(res.data.filter((c) => c.status === "COMPLETED"));
        }
      } catch {
        if (active) setError("Failed to load reports. Backend may be unreachable.");
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
          <h2 className="text-3xl font-bold tracking-tight text-white">Security Reports</h2>
          <p className="text-slate-400 mt-1 text-sm">
            Assessment analytics and vulnerability summaries for completed campaigns.
          </p>
        </div>
        <button onClick={fetchCampaigns} className="btn-secondary">
          <RefreshCcw className="w-3.5 h-3.5" /> Refresh
        </button>
      </div>

      {error && <ErrorState message={error} onRetry={fetchCampaigns} />}

      {!error && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {loading ? (
            <div className="col-span-full py-12 flex justify-center">
              <Loader2 className="w-5 h-5 text-cyan-400 animate-spin" />
            </div>
          ) : campaigns.length === 0 ? (
            <div className="col-span-full">
              <EmptyState message="No completed campaigns yet. Finish a campaign to generate a report." />
            </div>
          ) : (
            campaigns.map((camp) => (
              <div key={camp.id} className="card p-5 flex flex-col">
                <div className="flex justify-between items-start mb-4">
                  <div className="p-2.5 bg-violet-500/10 border border-violet-500/20 rounded-lg">
                    <FileBarChart className="w-5 h-5 text-violet-400" />
                  </div>
                  <StatusBadge status={camp.status} />
                </div>
                <h3 className="text-base font-semibold text-slate-200 mb-1 truncate">{camp.name}</h3>
                <p className="text-xs text-slate-500 mb-5">
                  {camp.created_at ? new Date(camp.created_at).toLocaleString() : "Unknown"}
                </p>
                <div className="mt-auto pt-4 border-t border-slate-800">
                  <Link
                    href={`/reports/${camp.id}`}
                    className="block w-full py-2 bg-slate-800 text-slate-200 rounded-lg hover:bg-slate-700 text-center text-sm font-medium transition-colors"
                  >
                    View Full Report
                  </Link>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
