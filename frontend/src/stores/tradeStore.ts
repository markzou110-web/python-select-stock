import { create } from 'zustand';
import api from '@/lib/api';

export interface Trade {
    id: number;
    code: string;
    name: string;
    entry_price: number;
    current_price: number;
    entry_date: string;
    pl: number;
    pl_pct: number;
    hold_days: number;
    industry: string;
    status: string;
    close_price?: number;
    close_date?: string;
}

interface TradeStore {
    trades: Trade[];
    loading: boolean;
    stats: Record<string, any>;
    tab: 'open' | 'closed';
    closingId: number | null;
    closePrice: string;
    fetchTrades: (showRefresh?: boolean) => Promise<void>;
    setTab: (tab: 'open' | 'closed') => void;
    removeTrade: (id: number) => Promise<void>;
    closeTrade: (id: number, closePrice: number) => Promise<void>;
    setClosingId: (id: number | null) => void;
    setClosePrice: (price: string) => void;
}

export const useTradeStore = create<TradeStore>((set, get) => ({
    trades: [],
    loading: true,
    stats: {
        total_trades: 0, wins: 0, losses: 0, flat: 0,
        win_rate: 0, avg_pl_pct: 0, total_pl_pct: 0, avg_hold_days: 0,
        best_trade: null, worst_trade: null, sector_distribution: [],
    },
    tab: 'open',
    closingId: null,
    closePrice: '',

    fetchTrades: async (showRefresh = false) => {
        if (showRefresh) set({ loading: true });
        try {
            const res = await api.get('/api/paper/list');
            set({ trades: res.data.trades || [], stats: res.data.stats || get().stats, loading: false });
        } catch {
            set({ loading: false });
        }
    },
    setTab: (tab) => set({ tab }),
    removeTrade: async (id) => {
        await api.delete(`/api/paper/remove/${id}`);
        get().fetchTrades();
    },
    closeTrade: async (id, closePrice) => {
        await api.post(`/api/paper/close/${id}`, { close_price: closePrice });
        set({ closingId: null, closePrice: '' });
        get().fetchTrades();
    },
    setClosingId: (id) => set({ closingId: id }),
    setClosePrice: (price) => set({ closePrice: price }),
}));
