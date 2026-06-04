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
    signal?: string;
    tv_ma_signal?: string;
    tv_zp_signal?: string;
    tv_match?: string;
    early_watch_only?: boolean;
    early_watch_reason?: string;
    early_watch_quality_ok?: boolean;
    early_watch_quality_reasons?: string[];
    momentum_watch_only?: boolean;
    momentum_watch_reason?: string;
    sector_watch_only?: boolean;
    sector_watch_reason?: string;
    影线比?: number;
    strategy_type?: string;
    warnings?: string[];
    ROE?: number;
    净利YOY?: number;
    结构?: string;
    体质?: string;
    回测统计?: BacktestStats;
    // SOP 等级系统
    sop_grade?: 'A' | 'B' | 'M' | 'C' | 'D';
    sop_vetoes?: string[];
    sop_checks?: string[];
    sop_bonuses?: string[];
    entry_price?: number;
    stop_price?: number;
    plan_stop_price?: number;
    initial_stop_price?: number;
    structure_stop_price?: number;
    target_price?: number;
    risk_reward?: number;
    risk_notes?: string[];
    pct_5d?: number;
    mkt_cap_yi?: number;
    date?: string;
    日期?: string;
    sector_trend?: string;
    sector_pct?: number;
    sector_momentum_score?: number;
    sector_breadth?: number;
    sector_phase?: string;
    sector_rank?: number;
    sector_alignment_score?: number;
    sector_relative_pct?: number;
    sector_role?: 'LEADER' | 'CORE' | 'FOLLOWER' | 'LAGGARD' | string;
    sector_3d_pct?: number;
    sector_5d_pct?: number;
    sector_consecutive_up_days?: number;
    sector_trend_slope?: number;
    brooks_rank_adjustment?: number;
    final_rank_score?: number;
    price_action_score?: number;
    price_action_regime?: string;
    price_action_signal?: string;
    price_action_pattern?: string;
    price_action_entry_quality?: string;
    price_action_summary?: string;
    price_action_risks?: string[];
    pa_market_cycle?: string;
    pa_range_location?: string;
    pa_entry_price?: number;
    pa_stop_price?: number;
    pa_target_price?: number;
    pa_risk_reward?: number;
    pa_tags?: string[];
    pa_pullback_legs?: number;
    pa_pullback_structure?: string;
    pa_breakout_quality?: string;
    pa_failure_risk?: number;
    pa_entry_quality_score?: number;
    pa_h2_quality?: string;
    pa_range_rule?: string;
    pa_failed_breakout_type?: string | null;
    pa_trap_risk?: number;
    pa_micro_channel?: string;
    pa_always_in_strength?: number;
    pa_trend_damage?: string;
    pa_channel_state?: string;
    pa_position_strategy?: string;
    pa_weekly_context?: string;
    pa_multi_timeframe_score?: number;
    pa_multi_timeframe_note?: string;
    pa_volume_pattern?: string;
    pa_volume_confirmed?: boolean;
    pa_volume_risk?: string;
    pa_failed_second_entry?: string | null;
    pa_second_entry_risk?: number;
    pa_gap_type?: string;
    pa_gap_risk?: number;
    pa_range_width_quality?: string;
    pa_range_center_risk?: number;
    pa_range_failed_breakout_count?: number;
    pa_trend_phase?: string;
    pa_trend_phase_action?: string;
    pa_decision_summary?: string;
    pa_trade_action?: 'READY' | 'WATCH' | 'WAIT' | 'AVOID' | string;
    pa_trade_setup?: string;
    pa_risk_pct?: number;
    pa_trade_plan?: {
        action: 'READY' | 'WATCH' | 'WAIT' | 'AVOID';
        action_label: string;
        setup: string;
        quality: string;
        entry_condition: string;
        invalidation: string;
        risk_pct: number;
        risk_reward: number;
        position_hint: string;
        checklist: string[];
        management: string[];
        avoid_reasons: string[];
    };
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
    strategy_type: 'tv_dual_strict' | 'tv_dual' | 'sector_watch' | 'squeeze' | 'pine' | 'both' | 'consensus' | 'tv_zp';
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

interface ScanPreflightCheck {
    name: string;
    status: 'ok' | 'warn' | 'error';
    message: string;
}

interface ScanPreflightResult {
    status: 'ok' | 'warn' | 'error';
    blocking: boolean;
    message: string;
    checks: ScanPreflightCheck[];
    summary: {
        selected_date?: string;
        stock_count?: number;
        ready_history_count?: number;
    };
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
    viewMode: 'list' | 'grid' | 'history';
    isFilterOpen: boolean;

    setResults: (results: ScanResult[]) => void;
    setIsScanning: (v: boolean) => void;
    setSelectedStock: (stock: ScanResult | null) => void;
    setParams: (params: ScanParams) => void;
    setViewMode: (mode: 'list' | 'grid' | 'history') => void;
    setIsFilterOpen: (v: boolean) => void;
    setSelectedDate: (date: string) => void;

    startScan: () => Promise<void>;
    loadHistory: (date: string) => Promise<void>;
    fetchHistory: () => Promise<void>;
    fetchAvailableDates: () => Promise<void>;
    handleExport: () => void;
}

const DEFAULT_PARAMS: ScanParams = {
    strategy_type: 'tv_dual_strict',
    pine_min_signals: 3,
    min_data_days: 120,
    threshold: 0.12,
    vol_multiplier: 1.5,
    rsi_min: 55,
    use_macd_filter: true,
    use_bb_sqz: false,
    sqz_lookback: 10,
    use_weekly: false,
    weekly_ma_period: 20,
    market_range: '全市场(除科创)',
    turnover_min: 3.0,
    mkt_cap_min: 0,
    use_rs_filter: false,
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
        set({ isScanning: true, results: [], scanProgress: { current: 0, total: 100, matches: 0, elapsed: 0, message: "正在执行扫描预检..." } });
        const startTime = Date.now();

        try {
            const cleanParams: Partial<ScanParams> = { ...get().params };
            Object.keys(cleanParams).forEach(key => {
                const paramKey = key as keyof ScanParams;
                const val = cleanParams[paramKey];
                if (typeof val === 'number' && isNaN(val)) {
                    delete cleanParams[paramKey];
                }
            });

            const preflightRes = await api.get<ScanPreflightResult>('/api/scan/preflight', {
                params: {
                    data_date: cleanParams.data_date || undefined,
                    min_history_days: cleanParams.min_data_days || 120,
                }
            });
            const preflight = preflightRes.data;
            if (preflight.blocking) {
                alert(`扫描预检未通过\n\n${preflight.checks.map(item => item.message).join('\n')}`);
                set({ isScanning: false, scanProgress: null });
                return;
            }
            if (preflight.status === 'warn') {
                const shouldContinue = window.confirm(
                    `扫描预检存在风险：\n\n${preflight.checks.map(item => item.message).join('\n')}\n\n仍要继续扫描吗？`
                );
                if (!shouldContinue) {
                    set({ isScanning: false, scanProgress: null });
                    return;
                }
            }

            set({ scanProgress: { current: 0, total: 100, matches: 0, elapsed: 0, message: "正在向后台提交扫描任务..." } });

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

            const submitRes = await marketApi.scanMarket(cleanParams);
            
            if (submitRes.data.task_id) {
                const taskId = submitRes.data.task_id;
                let consecutivePollFailures = 0;
                
                // Poll for status
                return new Promise<void>((resolve) => {
                    const pollInterval = setInterval(async () => {
                        try {
                            const statusRes = await api.get(`/api/scan/status/${taskId}`);
                            consecutivePollFailures = 0;
                            const state = statusRes.data.status;
                            
                            if (state === 'SUCCESS') {
                                clearInterval(pollInterval);
                                // Allow progress animation to complete
                                setTimeout(() => {
                                    set({
                                        results: statusRes.data.results || [],
                                        selectedDate: new Date().toISOString().split('T')[0],
                                        isScanning: false,
                                        scanProgress: null,
                                    });
                                    get().fetchHistory(); // Refresh date list
                                    if (ws) { try { ws.close(); } catch {} }
                                    resolve();
                                }, 1000);
                            } else if (state === 'FAILURE') {
                                clearInterval(pollInterval);
                                const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
                                alert(`扫描失败 (耗时: ${elapsed}秒)\n\n${statusRes.data.message}`);
                                set({ isScanning: false, scanProgress: null });
                                if (ws) { try { ws.close(); } catch {} }
                                resolve();
                            }
                            // PENDING or STARTED: just wait
                        } catch (err) {
                            consecutivePollFailures += 1;
                            const elapsed = Math.floor((Date.now() - startTime) / 1000);
                            set({
                                scanProgress: {
                                    current: 0,
                                    total: 100,
                                    matches: 0,
                                    elapsed,
                                    message: `后端连接恢复中... (${consecutivePollFailures}/10)`,
                                },
                            });
                            console.warn("Scan status polling failed; will retry", err);
                            if (consecutivePollFailures >= 10) {
                                clearInterval(pollInterval);
                                alert("扫描状态连接中断，请确认后端服务正常后重新扫描。");
                                set({ isScanning: false, scanProgress: null });
                                if (ws) { try { ws.close(); } catch {} }
                                resolve();
                            }
                        }
                    }, 1000);
                });
            } else {
                // Fallback for sync return if celery was disabled temporarily
                setTimeout(() => {
                    set({
                        results: submitRes.data.results || submitRes.data || [],
                        selectedDate: new Date().toISOString().split('T')[0],
                        isScanning: false,
                        scanProgress: null,
                    });
                    get().fetchHistory();
                    if (ws) { try { ws.close(); } catch {} }
                }, 1000);
            }
        } catch (e: unknown) {
            const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
            console.error("Scan Error Detail:", e);
            const scanError = e as {
                code?: string;
                message?: string;
                response?: { status?: number; data?: { detail?: string } };
            };
            const errorMsg = scanError.response?.data?.detail || scanError.message || "未知错误";

            let fullMessage = `请求失败 (耗时: ${elapsed}秒)\n\n`;
            if (scanError.code === 'ECONNABORTED' || scanError.message?.includes('timeout')) {
                fullMessage += `错误类型: 请求超时\n\n请检查网络并重试`;
            } else if (scanError.response?.status === 503) {
                fullMessage += `错误类型: 服务不可用\n\n${errorMsg}`;
            } else if (scanError.response?.status === 400) {
                fullMessage += `错误类型: 参数错误\n\n${errorMsg}`;
            } else {
                fullMessage += `错误: ${errorMsg}`;
            }
            alert(fullMessage);
            set({ isScanning: false, scanProgress: null });
            if (ws) { try { ws.close(); } catch {} }
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
