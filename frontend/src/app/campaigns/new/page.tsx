"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Play, Database } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import { SetupDemoResponse } from "@/lib/types";

export default function NewCampaignPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [setupLoading, setSetupLoading] = useState(false);
  
  const [formData, setFormData] = useState({
    name: "Standard Injection Test",
    project_id: "",
    target_id: "",
    attack_budget: 10,
    exploration_ratio: 0.4
  });

  const handleSetupDemo = async () => {
    setSetupLoading(true);
    try {
      const res = await api.post<SetupDemoResponse>("/campaigns/setup-demo");
      setFormData(prev => ({
        ...prev,
        project_id: res.data.project_id,
        target_id: res.data.target_id
      }));
      alert("Demo Project and Target created successfully!");
    } catch (error) {
      console.error(error);
      alert("Failed to setup demo environment.");
    } finally {
      setSetupLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.project_id || !formData.target_id) {
      alert("Please provide Project ID and Target ID, or click 'Setup Demo IDs'.");
      return;
    }

    setLoading(true);
    try {
      await api.post("/campaigns/start", formData);
      router.push("/campaigns");
    } catch (error) {
      console.error(error);
      alert("Failed to start campaign.");
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/campaigns" className="p-2 hover:bg-slate-200 rounded-full transition-colors text-slate-500">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">Start New Campaign</h2>
          <p className="text-slate-500 text-sm mt-1">Configure and launch a new automated red teaming run.</p>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <div className="mb-6 p-4 bg-blue-50 border border-blue-100 rounded-lg flex items-center justify-between">
          <div>
            <h4 className="text-sm font-semibold text-blue-900">Need Test Data?</h4>
            <p className="text-xs text-blue-700 mt-1">Generate dummy Project and Target IDs to pass database constraints.</p>
          </div>
          <button 
            type="button" 
            onClick={handleSetupDemo}
            disabled={setupLoading}
            className="px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
          >
            <Database className="w-3 h-3" />
            {setupLoading ? "Setting up..." : "Setup Demo IDs"}
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Campaign Name</label>
            <input 
              type="text" 
              required
              value={formData.name}
              onChange={e => setFormData({...formData, name: e.target.value})}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Project ID (UUID)</label>
              <input 
                type="text" 
                required
                value={formData.project_id}
                onChange={e => setFormData({...formData, project_id: e.target.value})}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Target ID (UUID)</label>
              <input 
                type="text" 
                required
                value={formData.target_id}
                onChange={e => setFormData({...formData, target_id: e.target.value})}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Attack Budget</label>
              <input 
                type="number" 
                min="1" max="500"
                required
                value={formData.attack_budget}
                onChange={e => setFormData({...formData, attack_budget: parseInt(e.target.value)})}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg outline-none"
              />
              <p className="text-xs text-slate-500 mt-1">Total number of prompt attempts.</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Exploration Ratio</label>
              <input 
                type="number" 
                step="0.1" min="0" max="1"
                required
                value={formData.exploration_ratio}
                onChange={e => setFormData({...formData, exploration_ratio: parseFloat(e.target.value)})}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg outline-none"
              />
              <p className="text-xs text-slate-500 mt-1">0.0 = Pure exploitation, 1.0 = Pure random exploration.</p>
            </div>
          </div>

          <div className="pt-4 mt-6 border-t border-slate-100 flex justify-end gap-3">
            <Link href="/campaigns" className="px-4 py-2 text-slate-600 font-medium text-sm hover:bg-slate-100 rounded-lg transition-colors">
              Cancel
            </Link>
            <button 
              type="submit" 
              disabled={loading}
              className="px-4 py-2 bg-slate-900 text-white rounded-lg hover:bg-slate-800 disabled:opacity-50 flex items-center gap-2 text-sm font-medium transition-colors"
            >
              {loading ? "Starting..." : <><Play className="w-4 h-4" /> Start Campaign</>}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}