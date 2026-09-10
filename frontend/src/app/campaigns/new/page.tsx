"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Play, Database, Loader2, AlertCircle, Check } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SetupDemoResponse, AttackStrategy, CampaignStartResponse } from "@/lib/types";

export default function NewCampaignPage() {
  const router = useRouter();
  const { isAuthenticated, loading: authLoading } = useAuth();
  const [loading, setLoading] = useState(false);
  const [setupLoading, setSetupLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [strategies, setStrategies] = useState<AttackStrategy[]>([]);
  const [strategiesLoading, setStrategiesLoading] = useState(true);

  const [formData, setFormData] = useState({
    name: "",
    project_id: "",
    target_id: "",
    attack_budget: 20,
    exploration_ratio: 0.3,
  });

  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);

  useEffect(() => {
    if (!authLoading && !isAuthenticated) router.push("/login");
  }, [authLoading, isAuthenticated, router]);

  useEffect(() => {
    if (!isAuthenticated) return;
    const fetchStrategies = async () => {
      try {
        const res = await api.get<AttackStrategy[]>("/campaigns/strategies");
        setStrategies(res.data);
        setSelectedStrategies(res.data.map((s) => s.name));
      } catch {
        console.error("Failed to fetch strategies");
      } finally {
        setStrategiesLoading(false);
      }
    };
    fetchStrategies();
  }, [isAuthenticated]);

  const handleSetupDemo = async () => {
    setSetupLoading(true);
    setError("");
    try {
      const res = await api.post<SetupDemoResponse>("/campaigns/setup-demo");
      setFormData((prev) => ({
        ...prev,
        project_id: res.data.project_id,
        target_id: res.data.target_id,
      }));
      setSuccess("Demo environment created. IDs auto-filled.");
    } catch {
      setError("Failed to setup demo environment.");
    } finally {
      setSetupLoading(false);
    }
  };

  const toggleStrategy = (name: string) => {
    setSelectedStrategies((prev) =>
      prev.includes(name) ? prev.filter((s) => s !== name) : [...prev, name]
    );
  };

  const complexityColor: Record<string, string> = {
    low: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    medium: "text-amber-400 bg-amber-500/10 border-amber-500/20",
    high: "text-orange-400 bg-orange-500/10 border-orange-500/20",
    very_high: "text-red-400 bg-red-500/10 border-red-500/20",
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!formData.project_id || !formData.target_id) {
      setError("Project ID and Target ID are required. Use Setup Demo to generate test IDs.");
      return;
    }
    setLoading(true);
    try {
      const res = await api.post<CampaignStartResponse>("/campaigns/start", {
        name: formData.name || "Unnamed Campaign",
        project_id: formData.project_id,
        target_id: formData.target_id,
        attack_budget: formData.attack_budget,
        exploration_ratio: formData.exploration_ratio,
      });
      router.push(`/campaigns/${res.data.experiment_id}`);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setError(axiosErr.response?.data?.detail || "Failed to start campaign. Check your configuration.");
      setLoading(false);
    }
  };

  if (authLoading || !isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/campaigns" className="btn-ghost px-2">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-white">New Campaign</h2>
          <p className="text-slate-400 text-sm mt-1">Configure and launch a new red teaming run.</p>
        </div>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
          <span className="text-xs text-red-400">{error}</span>
        </div>
      )}

      {success && (
        <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center gap-2">
          <Check className="w-4 h-4 text-emerald-400 shrink-0" />
          <span className="text-xs text-emerald-400">{success}</span>
        </div>
      )}

      <div className="card p-6">
        <div className="mb-5 p-4 bg-cyan-500/5 border border-cyan-500/20 rounded-lg flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-semibold text-cyan-400">Quick Setup</h4>
            <p className="text-xs text-slate-400 mt-0.5">Generate a demo project and target to get started quickly.</p>
          </div>
          <button
            type="button"
            onClick={handleSetupDemo}
            disabled={setupLoading}
            className="btn-secondary shrink-0"
          >
            <Database className="w-3.5 h-3.5" />
            {setupLoading ? "Setting up..." : "Setup Demo IDs"}
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wider">
              Campaign Name
            </label>
            <input
              type="text"
              required
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input-dark"
              placeholder="e.g. GPT-4 Injection Assessment"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wider">
                Project ID
              </label>
              <input
                type="text"
                required
                value={formData.project_id}
                onChange={(e) => setFormData({ ...formData, project_id: e.target.value })}
                className="input-dark font-mono text-xs"
                placeholder="UUID"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wider">
                Target ID
              </label>
              <input
                type="text"
                required
                value={formData.target_id}
                onChange={(e) => setFormData({ ...formData, target_id: e.target.value })}
                className="input-dark font-mono text-xs"
                placeholder="UUID"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wider">
                Attack Budget
              </label>
              <input
                type="number"
                min="1"
                max="500"
                required
                value={formData.attack_budget}
                onChange={(e) => {
                  const budget = parseInt(e.target.value, 10);
                  setFormData({
                    ...formData,
                    attack_budget: Number.isNaN(budget) ? 1 : budget,
                  });
                }}
                className="input-dark"
              />
              <p className="text-[10px] text-slate-500 mt-1">Maximum number of attack attempts (1-500).</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wider">
                Exploration Ratio
              </label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="1"
                required
                value={formData.exploration_ratio}
                onChange={(e) => {
                  const ratio = parseFloat(e.target.value);
                  setFormData({
                    ...formData,
                    exploration_ratio: Number.isNaN(ratio) ? 0.3 : ratio,
                  });
                }}
                className="input-dark"
              />
              <p className="text-[10px] text-slate-500 mt-1">0.0 = exploit only, 1.0 = explore only.</p>
            </div>
          </div>

          <div className="pt-2">
            <label className="block text-xs font-medium text-slate-400 mb-3 uppercase tracking-wider">
              Attack Strategies ({selectedStrategies.length}/{strategies.length})
            </label>
            {strategiesLoading ? (
              <div className="flex items-center gap-2 py-4">
                <Loader2 className="w-4 h-4 text-cyan-400 animate-spin" />
                <span className="text-xs text-slate-500">Loading strategies...</span>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2">
                {strategies.map((strategy) => {
                  const selected = selectedStrategies.includes(strategy.name);
                  return (
                    <button
                      key={strategy.name}
                      type="button"
                      onClick={() => toggleStrategy(strategy.name)}
                      className={`p-3 rounded-lg border text-left transition-all ${
                        selected
                          ? "bg-cyan-500/5 border-cyan-500/30"
                          : "bg-slate-900/50 border-slate-800 hover:border-slate-700"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <div
                              className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
                                selected
                                  ? "bg-cyan-500 border-cyan-500"
                                  : "border-slate-600"
                              }`}
                            >
                              {selected && <Check className="w-2.5 h-2.5 text-white" />}
                            </div>
                            <span className="text-sm font-medium text-slate-200 truncate">
                              {strategy.name.replace(/_/g, " ")}
                            </span>
                            <span className={`px-1.5 py-0.5 text-[9px] font-semibold uppercase rounded border ${complexityColor[strategy.metadata.complexity] || ""}`}>
                              {strategy.metadata.complexity}
                            </span>
                          </div>
                          <p className="text-[11px] text-slate-500 mt-1 ml-6">
                            {strategy.metadata.description}
                          </p>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-slate-800 flex justify-end gap-3">
            <Link href="/campaigns" className="btn-ghost">
              Cancel
            </Link>
            <button type="submit" disabled={loading} className="btn-primary">
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {loading ? "Starting..." : "Start Campaign"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
