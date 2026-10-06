import { create } from 'zustand';
import api, { marketApi } from '@/lib/api';
import type { ScanParams } from '@/stores/scanStore';

export interface IndexData {
    price: number;
    pct: number;
}

export interface SyncProgress {
    is_running: boolean;
    total: number;
    current: number;
    success: number;
    fail: number;
    start_time: string | null;
    status_text: string;
    stop_requested: boolean;
}

export interface MarketRegimeData {
    status: string;
    desc: string;
    indices: Record<string, {
        close?: number;
        ema20?: number;
        trend?: string;
        trend_label?: string;
        ema20_gap_pct?: number;
        chg_pct?: number;
    }>;
    baseline_label?: string;
    trend_basis?: string;
    updated_at: string;
}

interface MarketStore {
    indices: Record<string, IndexData>;
    sectors: Array<{ name: string; pct: number; lead: string }>;
    syncProgress: SyncProgress | null;
    marketRegime: MarketRegimeData | null;
    loading: boolean;
    lastUpdated: string;
    // 最近一次行情类请求是否失败；成功后清掉，供 Dashboard 渲染错误横幅
    lastFetchFailed: boolean;

    fetchMarketData: () => Promise<void>;
    fetchSyncStatus: () => Promise<void>;
    fetchMarketPulse: () => Promise<void>;
    fetchMarketRegime: (strategyType?: string) => Promise<{
        regime: { label: string; description: string };
        recommended_params: Partial<ScanParams> & { description?: string };
    } | null>;
    startSync: () => Promise<void>;
    startSyncFundamentals: () => Promise<void>;
    stopSync: () => Promise<void>;
    setLastUpdated: (t: string) => void;
}

export const useMarketStore = create<MarketStore>((set, get) => ({
    indices: {},
    sectors: [],
    syncProgress: null,
    marketRegime: null,
    loading: true,
    lastUpdated: '',
    lastFetchFailed: false,

    setLastUpdated: (t) => set({ lastUpdated: t }),

    fetchMarketData: async () => {
        // Try localStorage cache first for instant rendering
        try {
            const cachedIndices = localStorage.getItem('av_indices_cache');
            const cachedSectors = localStorage.getItem('av_sectors_cache');
            if (cachedIndices) set({ indices: JSON.parse(cachedIndices) });
            if (cachedSectors) set({ sectors: JSON.parse(cachedSectors) });
        } catch (e) {
            console.error("Local cache error:", e);
        }

        try {
            const [idxRes, secRes] = await Promise.all([
                marketApi.getIndices(),
                marketApi.getSectors()
            ]);
            set({ indices: idxRes.data, sectors: secRes.data, lastFetchFailed: false });

            // Update cache
            localStorage.setItem('av_indices_cache', JSON.stringify(idxRes.data));
            localStorage.setItem('av_sectors_cache', JSON.stringify(secRes.data));
        } catch (e) {
            console.error("Failed to fetch market data:", e);
            set({ lastFetchFailed: true });
        }
    },

    fetchSyncStatus: async () => {
        try {
            const res = await api.get('/api/sync/status');
            set({ syncProgress: res.data, lastFetchFailed: false });
        } catch (e) {
            console.error("Sync status fetch failed", e);
            set({ lastFetchFailed: true });
        }
    },

    fetchMarketPulse: async () => {
        try {
            const res = await api.get('/api/market/pulse');
            if (res.data && res.data.status) {
                set({ marketRegime: res.data, lastFetchFailed: false });
            }
        } catch (e) {
            console.error("Market pulse fetch failed", e);
            set({ lastFetchFailed: true });
        }
    },

    fetchMarketRegime: async (strategyType = "squeeze") => {
        try {
            const res = await api.get(`/api/market/regime?strategy_type=${strategyType}`);
            set({ lastFetchFailed: false });
            return res.data || null;
        } catch (e) {
            console.error("Market regime fetch failed", e);
            set({ lastFetchFailed: true });
            return null;
        }
    },

    startSync: async () => {
        try {
            await api.post('/api/sync/daily');
            get().fetchSyncStatus();
        } catch (e) {
            console.error("Sync start failed", e);
        }
    },

    startSyncFundamentals: async () => {
        try {
            await api.post('/api/sync/fundamentals');
            get().fetchSyncStatus();
        } catch (e) {
            console.error("Fundamental sync start failed", e);
        }
    },

    stopSync: async () => {
        try {
            await api.post('/api/sync/stop');
            get().fetchSyncStatus();
        } catch (e) {
            console.error("Sync stop failed", e);
        }
    },
}));
