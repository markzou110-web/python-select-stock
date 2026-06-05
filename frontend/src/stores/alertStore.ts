import { create } from 'zustand';
import api from '@/lib/api';

export type AlertLevel = 'CRITICAL' | 'WARNING' | 'INFO';

export interface AlertItem {
    id: number;
    code: string;
    name: string;
    level: string; // 'critical' or 'warning'
    priority?: 'P0' | 'P1' | 'P2' | string;
    priority_label?: string;
    dedup_key?: string;
    dedup_minutes?: number;
    reasons: string[];
    suggestion: string;
    action_line?: string;
    entry_price: number;
    current_price: number;
    pl_pct: number;
    ema20: number;
    timestamp: string;
    dismissed: boolean;
}

type AlertResponseItem = Omit<AlertItem, 'dismissed'> & { dismissed?: boolean };

interface AlertStore {
    alerts: AlertItem[];
    loading: boolean;
    unreadCount: number;

    fetchAlerts: () => Promise<void>;
    dismissAlert: (id: number) => void;
    dismissAll: () => void;
}

export const useAlertStore = create<AlertStore>((set, get) => ({
    alerts: [],
    loading: false,
    unreadCount: 0,

    fetchAlerts: async () => {
        set({ loading: true });
        try {
            const res = await api.get('/api/alert/list');
            const alerts: AlertItem[] = ((res.data.alerts || []) as AlertResponseItem[]).map((a) => ({
                ...a,
                dismissed: false,
            }));
            set({
                alerts,
                unreadCount: alerts.filter(a => a.level === 'critical' || a.level === 'warning').length,
            });
        } catch (e) {
            console.error("Failed to fetch alerts:", e);
        } finally {
            set({ loading: false });
        }
    },

    dismissAlert: (id: number) => {
        const alerts = get().alerts.map(a => a.id === id ? { ...a, dismissed: true } : a);
        set({
            alerts,
            unreadCount: alerts.filter(a => !a.dismissed && (a.level === 'critical' || a.level === 'warning')).length,
        });
    },

    dismissAll: () => {
        const alerts = get().alerts.map(a => ({ ...a, dismissed: true }));
        set({ alerts, unreadCount: 0 });
    },
}));
