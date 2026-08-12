"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FileBarChart, RefreshCcw } from "lucide-react";
import { api } from "@/lib/api";
import { Campaign } from "@/lib/types";

export default function ReportsListPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchCampaigns = async () => {
    setLoading(true);
    try {
      const res = await api.get<Campaign[]>("/campaigns");
      setCampaigns(res.data);
    } catch (error) {
      console.error("Failed to fetch campaigns for reports:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCampaigns();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-slate-900">Security Reports</h2>
          <p className="text-slate-500 mt-2">View assessment analytics and vulnerability summaries for your campaigns.</p>
        </div>
        <button 
          onClick={fetchCampaigns}
          className="px-4 py-2 bg-white border border-slate-200 text-slate-700 rounded-lg hover:bg-slate-50 flex items-center gap-2 text-sm font-medium transition-colors"
        >
          <RefreshCcw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {loading ? (
          <p className="text-slate-500">Loading available reports...</p>
        ) : campaigns.length === 0 ? (
          <p className="text-slate-500">No campaigns found. Start a campaign to generate a report.</p>
        ) : (
          campaigns.map((camp) => (
            <div key={camp.id} className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex flex-col">
              <div className="flex justify-between items-start mb-4">
                <div className="p-3 bg-indigo-50 text-indigo-600 rounded-lg">
                  <FileBarChart className="w-6 h-6" />
                </div>
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                  camp.status === 'COMPLETED' ? 'bg-emerald-100 text-emerald-800' :
                  camp.status === 'RUNNING' ? 'bg-blue-100 text-blue-800' :
                  'bg-slate-100 text-slate-800'
                }`}>
                  {camp.status}
                </span>
              </div>
              
              <h3 className="text-lg font-semibold text-slate-900 mb-1">{camp.name}</h3>
              <p className="text-sm text-slate-500 mb-6">Generated: {new Date(camp.created_at).toLocaleDateString()}</p>
              
              <div className="mt-auto pt-4 border-t border-slate-100">
                <Link 
                  href={`/reports/${camp.id}`}
                  className="w-full py-2 bg-slate-900 text-white rounded-lg hover:bg-slate-800 flex justify-center items-center text-sm font-medium transition-colors"
                >
                  View Full Report
                </Link>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}