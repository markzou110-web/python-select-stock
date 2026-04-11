import { create } from 'zustand';
import api, { marketApi, connectScanWebSocket } from '@/lib/api';

export interface ScanResult {
    代码: string;
    名称: string;
    行业: string;
    现价: number;
    '涨幅%': number;
    Score: number;
    RSI: number;
    DIF: number;
    BB: number;
    粘合度: number;
    历史胜率: string;
    信号次数: number;
    北向?: string;
    共振?: string;
    影线比?: number;
    strategy_type?: string;
    warnings?: string[];
    结构?: string;
    体质?: string;
    回测统计?: BacktestStats;
}

export interface BacktestStats {
    win_rate: number;
    signal_count: number;
    avg_hold_days: number;
    avg_return: number;
    max_drawdown: number;
    profit_factor: number;
    stop_loss_hits: number;
}

export interface ScanParams {
    strategy_type: 'squeeze' | 'pine' | 'both' | 'consensus';
    pine_min_signals: number;
    min_data_days: number;
    threshold: number;
    vol_multiplier: number;
    rsi_min: number;
    use_macd_filter: boolean;
    use_bb_sqz: boolean;
    sqz_lookback: number;
    use_weekly: boolean;
    weekly_ma_period: number;
    market_range: string;
    turnover_min: number;
    mkt_cap_min: number;
    use_rs_filter: boolean;
    local_only: boolean;
    data_date: string;
    stop_loss_pct: number;
}

export interface ScanProgress {
    current: number;
    total: number;
    matches: number;
    elapsed: number;
    message?: string;
}

interface ScanStore {
    results: ScanResult[];
    isScanning: boolean;
    selectedStock: ScanResult | null;
    params: ScanParams;
    historyDates: string[];
    selectedDate: string;
    availableDates: Array<{ date: string; stock_count: number }>;
    scanProgress: ScanProgress | null;
    viewMode: 'list' | 'grid';
    isFilterOpen: boolean;

    setResults: (results: ScanResult[]) => void;
    setIsScanning: (v: boolean) => void;
    setSelectedStock: (stock: ScanResult | null) => void;
    setParams: (params: ScanParams) => void;
    setViewMode: (mode: 'list' | 'grid') => void;
    setIsFilterOpen: (v: boolean) => void;
    setSelectedDate: (date: string) => void;

    startScan: () => Promise<void>;
    loadHistory: (date: string) => Promise<void>;
    fetchHistory: () => Promise<void>;
    fetchAvailableDates: () => Promise<void>;
    handleExport: () => void;
}

const DEFAULT_PARAMS: ScanParams = {
    strategy_type: 'squeeze',
    pine_min_signals: 3,
    min_data_days: 60,
    threshold: 0.12,
    vol_multiplier: 1.5,
    rsi_min: 55,
    use_macd_filter: true,
    use_bb_sqz: true,
    sqz_lookback: 10,
    use_weekly: false,
    weekly_ma_period: 20,
    market_range: '全市场(除科创)',
    turnover_min: 3.0,
    mkt_cap_min: 0,
    use_rs_filter: true,
    local_only: true,
    data_date: '',
    stop_loss_pct: -8,
};

export const useScanStore = create<ScanStore>((set, get) => ({
    results: [],
    isScanning: false,
    selectedStock: null,
    params: DEFAULT_PARAMS,
    historyDates: [],
    selectedDate: '',
    availableDates: [],
    scanProgress: null,
    viewMode: 'list',
    isFilterOpen: false,

    setResults: (results) => set({ results }),
    setIsScanning: (isScanning) => set({ isScanning }),
    setSelectedStock: (selectedStock) => set({ selectedStock }),
    setParams: (params) => set({ params }),
    setViewMode: (viewMode) => set({ viewMode }),
    setIsFilterOpen: (isFilterOpen) => set({ isFilterOpen }),
    setSelectedDate: (selectedDate) => set({ selectedDate }),

    startScan: async () => {
        let ws: WebSocket | null = null;
        set({ isScanning: true, results: [], scanProgress: { current: 0, total: 100, matches: 0, elapsed: 0, message: "正在初始化后台扫描引擎..." } });
        const startTime = Date.now();

        try {
            ws = connectScanWebSocket((msg) => {
                const elapsed = Math.floor((Date.now() - startTime) / 1000);
                if (msg.type === 'scan_start') {
                    set({ scanProgress: { current: 0, total: 100, matches: 0, elapsed, message: msg.message } });
                } else if (msg.type === 'scan_progress') {
                    set({ scanProgress: { current: msg.current, total: msg.total, matches: 0, elapsed, message: msg.message } });
                } else if (msg.type === 'scan_end') {
                    set({ scanProgress: { current: 100, total: 100, matches: msg.matches, elapsed, message: msg.message } });
                }
            });

            // Clean NaN params before sending
            const cleanParams = { ...get().params } as any;
            Object.keys(cleanParams).forEach(key => {
                const val = cleanParams[key];
                if (typeof val === 'number' && isNaN(val)) {
                    delete cleanParams[key];
                }
            });

            const res = await marketApi.scanMarket(cleanParams);
            
            // Allow progress animation to complete
            setTimeout(() => {
                set({
                    results: res.data.results || [],
                    selectedDate: new Date().toISOString().split('T')[0],
                    isScanning: false,
                    scanProgress: null,
                });
                get().fetchHistory(); // Refresh date list
            }, 1000);
        } catch (e: any) {
            const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
            console.error("Scan Error Detail:", e);
            const errorMsg = e.response?.data?.detail || e.message;

            let fullMessage = `扫描失败 (耗时: ${elapsed}秒)\n\n`;
            if (e.code === 'ECONNABORTED' || e.message?.includes('timeout')) {
                fullMessage += `错误类型: 请求超时\n\n可能原因:\n`;
                fullMessage += `1. 扫描股票数量过多，请缩小市场范围或调高筛选条件\n`;
                fullMessage += `2. 网络连接不稳定，请检查网络设置\n`;
                fullMessage += `3. 后端处理缓慢，请查看后端日志\n\n`;
                fullMessage += `建议操作:\n- 勾选"仅本地数据"选项\n- 提高"最小换手率"阈值\n- 选择"沪深300"等较小市场范围`;
            } else if (e.response?.status === 503) {
                fullMessage += `错误类型: 服务不可用\n\n${errorMsg}`;
            } else if (e.response?.status === 400) {
                fullMessage += `错误类型: 参数错误\n\n${errorMsg}`;
            } else {
                fullMessage += `错误: ${errorMsg}`;
            }
            alert(fullMessage);
            set({ isScanning: false, scanProgress: null });
        } finally {
            if (ws) {
                try { ws.close(); } catch (e) {}
            }
        }
    },

    loadHistory: async (date: string) => {
        set({ selectedDate: date, isScanning: true });
        if (!date) {
            set({ isScanning: false });
            return;
        }
        try {
            const res = await api.get(`/api/scan/history?date=${date}`);
            set({ results: res.data });
        } catch (e) {
            console.error("Failed to load history", e);
        } finally {
            set({ isScanning: false });
        }
    },

    fetchHistory: async () => {
        try {
            const dateRes = await api.get('/api/scan/dates');
            const dates = dateRes.data;
            set({ historyDates: dates });

            // If we have history and no current results, load latest
            if (dates.length > 0 && get().results.length === 0) {
                const latestDate = dates[0];
                set({ selectedDate: latestDate });
                const res = await api.get(`/api/scan/history?date=${latestDate}`);
                set({ results: res.data });
            }
        } catch (e) {
            console.error("Failed to fetch history", e);
        }
    },

    fetchAvailableDates: async () => {
        try {
            const res = await api.get('/api/scan/available-dates');
            set({ availableDates: res.data.dates || [] });
        } catch (e) {
            console.error("Failed to fetch available dates", e);
        }
    },

    handleExport: () => {
        const { results } = get();
        if (results.length === 0) return;
        const headers = ["代码", "名称", "行业", "现价", "涨幅%", "综合强度", "RSI", "DIF", "BB", "粘合度", "历史胜率"];
        const rows = results.map(r => [
            r.代码, r.名称, r.行业, r.现价, r["涨幅%"], r.Score, r.RSI, r.DIF, r.BB, r.粘合度, r.历史胜率
        ]);
        const csvContent = [headers, ...rows].map(e => e.join(",")).join("\n");
        const blob = new Blob(["\ufeff" + csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", `AlphaVision_Results_${new Date().toLocaleDateString()}.csv`);
        link.click();
    },
}));
