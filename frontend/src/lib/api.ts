import axios from 'axios';
import type {
  AttackDetail,
  CampaignStatus,
  Finding,
  FindingDetail,
} from './types';

export const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('oracle_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      if (typeof window !== "undefined") {
        localStorage.removeItem("oracle_token");
        localStorage.removeItem("oracle_user");
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export function getCampaignStatus(campaignId: string) {
  return api.get<CampaignStatus>(`/campaigns/${campaignId}/status`);
}

export function getCampaignAttacks(campaignId: string) {
  return api.get<AttackDetail[]>(`/campaigns/${campaignId}/attacks`);
}

export function getCampaignFindings(campaignId: string) {
  return api.get<Finding[]>(`/campaigns/${campaignId}/findings`);
}

export function getVulnerabilityDetail(vulnerabilityId: string) {
  return api.get<FindingDetail>(`/vulnerabilities/${vulnerabilityId}`);
}
