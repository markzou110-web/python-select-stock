import { create } from 'zustand';
import api, { marketApi } from '@/lib/api';

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
    regime: string;
    label: string;
    color: string;
    description: string;
    details: any;
}

interface MarketStore {
    indices: Record<string, IndexData>;
    sectors: any[];
    syncProgress: SyncProgress | null;
    marketRegime: MarketRegimeData | null;
    loading: boolean;
    lastUpdated: string;

    fetchMarketData: () => Promise<void>;
    fetchSyncStatus: () => Promise<void>;
    fetchMarketRegime: (strategy_type: string) => Promise<any>;
    startSync: () => Promise<void>;
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
            set({ indices: idxRes.data, sectors: secRes.data });

            // Update cache
            localStorage.setItem('av_indices_cache', JSON.stringify(idxRes.data));
            localStorage.setItem('av_sectors_cache', JSON.stringify(secRes.data));
        } catch (e) {
            console.error("Failed to fetch market data:", e);
        }
    },

    fetchSyncStatus: async () => {
        try {
            const res = await api.get('/api/sync/status');
            set({ syncProgress: res.data });
        } catch (e) {
            console.error("Sync status fetch failed", e);
        }
    },

    fetchMarketRegime: async (strategy_type: string) => {
        try {
            const res = await api.get(`/api/market/regime?strategy_type=${strategy_type}`);
            if (res.data && res.data.regime) {
                set({ marketRegime: res.data.regime });
            }
            return res.data.recommended_params;
        } catch (e) {
            console.error("Market regime fetch failed", e);
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

    stopSync: async () => {
        try {
            await api.post('/api/sync/stop');
            get().fetchSyncStatus();
        } catch (e) {
            console.error("Sync stop failed", e);
        }
    },
}));
