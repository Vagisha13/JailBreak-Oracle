export interface Campaign {
  id: string;
  name: string;
  status: string;
  attack_budget: number;
  created_at: string;
}

export interface SetupDemoResponse {
  project_id: string;
  target_id: string;
  message: string;
}

export interface AttackStrategy {
  name: string;
  category: string;
  metadata: {
    description: string;
    complexity: string;
  };
}

export interface AttackDetail {
  id: string;
  strategy_name: string;
  category: string;
  prompt_text: string;
  parent_attack_id: string | null;
  round_number: number | null;
  mutation_type: string | null;
  created_at: string;
  target_response: string | null;
  latency_ms: number | null;
  is_jailbreak: boolean;
  severity: string | null;
  verified_status: string | null;
}

export interface UsageBreakdownItem {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface CampaignBudget {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  max_cost_usd: number | null;
  remaining_cost_usd: number;
  per_role: Record<string, UsageBreakdownItem>;
  per_model: Record<string, UsageBreakdownItem>;
}

export interface CampaignStatus {
  experiment_id: string;
  name: string;
  status: string;
  attack_budget: number;
  created_at: string;
  finished_at: string | null;
  current_round: number;
  total_rounds: number;
  heartbeat_at: string | null;
  is_stale: boolean;
  resumable: boolean;
  budget: CampaignBudget;
}

export interface FindingAttack {
  id: string;
  experiment_id: string;
  strategy_name: string;
  category: string;
  prompt_text: string;
  parent_attack_id: string | null;
  round_number: number | null;
  created_at: string;
  target_response: string | null;
  latency_ms: number | null;
}

export interface Finding {
  id: string;
  experiment_id: string;
  attack_id: string;
  category: string;
  severity: string;
  confidence: number;
  reasoning: string;
  verified_status: string;
  verification_reasoning: string | null;
  remediation_guidance: string | null;
  verifier_confidence: number | null;
  verified_at: string | null;
  created_at: string;
  evaluator_evidence: string[];
  verifier_evidence: string[];
}

export interface FindingDetail extends Finding {
  attack: FindingAttack | null;
}

export interface CampaignStartResponse {
  experiment_id: string;
  status: string;
  message: string;
}

export interface SeverityBreakdown {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface StrategyPerformance {
  strategy_name: string;
  total_attempts: number;
  successful_jailbreaks: number;
  success_rate_percentage: number;
}

export interface VulnerabilitySummaryItem {
  vulnerability_id: string;
  category: string;
  severity: string;
  confidence: number;
  reasoning: string;
  verified_status: string;
}

export interface RedTeamReport {
  experiment_id: string;
  experiment_name: string;
  target_id: string;
  target_name: string;
  generated_at: string;
  overall_risk_score: number;
  total_attacks_executed: number;
  jailbreak_success_rate: number;
  severity_breakdown: SeverityBreakdown;
  strategy_performance: StrategyPerformance[];
  top_vulnerabilities: VulnerabilitySummaryItem[];
  remediation_summary: string[];
}

export interface DashboardStats {
  active_campaigns: number;
  completed_reports: number;
  total_vulnerabilities: number;
}

export interface UserProfile {
  id: string;
  email: string;
  role: string;
}

export interface AuthToken {
  access_token: string;
  token_type: string;
}
