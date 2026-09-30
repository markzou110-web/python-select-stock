import { create } from 'zustand';
import api, { marketApi, connectScanWebSocket, type ScanMarketParams } from '@/lib/api';
import { downloadCsv, toCsvString } from '@/lib/csv';

export interface ScanResult {
    代码: string;
    名称: string;
    行业: string;
    现价: number;
    '涨幅%'?: number;
    Score: number;
    RSI?: number;
    DIF?: number;
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
    signal_sources?: Array<'ma' | 'zp'>;
    tv_execution_tier?: 'A' | 'B' | 'C';
    tv_execution_risk_unit?: number;
    early_watch_only?: boolean;
    early_watch_reason?: string;
    early_watch_quality_ok?: boolean;
    early_watch_quality_reasons?: string[];
    momentum_watch_only?: boolean;
    momentum_watch_reason?: string;
    momentum_acceleration_watch_only?: boolean;
    revival_watch_only?: boolean;
    result_group?: 'FORMAL' | 'HISTORICAL_REVIVAL' | 'MOMENTUM_WATCH' | 'SHADOW_RESEARCH';
    sector_watch_only?: boolean;
    sector_watch_reason?: string;
    bottom_discovery_watch_only?: boolean;
    bottom_discovery_stage?: 'B0_BASE' | 'B1_REVERSAL' | string;
    bottom_discovery_action?: string;
    bottom_discovery_metrics?: Record<string, number | boolean>;
    h2_watch_only?: boolean;
    sequoia_research_shadow_only?: boolean;
    release_state?: 'SHADOW' | string;
    shadow_instruction?: string;
    rps_60?: number;
    rps_120?: number;
    rps_250?: number;
    rps_sector_120?: number;
    rps_acceleration?: number;
    trader_vic_2b_metrics?: {
        vic_2b_support?: number | null;
        vic_2b_volume_ratio?: number | null;
        ma200?: number | null;
        ma200_slope_20d_pct?: number | null;
        rps_120_minimum?: number;
    };
    影线比?: number;
    strategy_type?: string;
    matched_strategies?: string[];
    warnings?: string[];
    ROE?: number;
    净利YOY?: number;
    结构?: string;
    体质?: string;
    回测统计?: BacktestStats;
    // 质量与风控明细（不再输出字母评级）
    sop_vetoes?: string[];
    sop_checks?: string[];
    sop_bonuses?: string[];
    sop_risks?: string[];
    sop_quality_score?: number;
    display_signal_score?: number;
    display_quality_score?: number;
    display_opportunity_score?: number;
    display_rank_score?: number;
    display_trade_score?: number;
    score_display_scale?: '0-100' | string;
    trade_bucket?: 'TRADE' | 'EARLY' | 'OBSERVE' | 'BLOCK' | string;
    trade_eligible?: boolean;
    trade_blockers?: string[];
    trade_cautions?: string[];
    money_flow_status?: string;
    money_flow?: {
        main_net_inflow_yi?: number;
        main_net_ratio?: number;
        source?: string;
    };
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
    data_date?: string;
    data_mode?: 'LIVE_SNAPSHOT' | 'LOCAL_DB' | string;
    as_of?: string;
    sector_trend?: string;
    sector_pct?: number;
    sector_momentum_score?: number;
    sector_breadth?: number;
    sector_phase?: string;
    sector_rank?: number;
    sector_alignment_score?: number;
    sector_relative_pct?: number;
    sector_role?: 'LEADER' | 'CORE' | 'FOLLOWER' | 'LAGGARD' | string;
    sector_mainline?: 'MAIN' | 'SECONDARY' | 'ROTATION' | 'FADING' | 'NON_MAIN' | string;
    leadership_score?: number;
    sector_3d_pct?: number;
    sector_5d_pct?: number;
    sector_consecutive_up_days?: number;
    sector_trend_slope?: number;
    brooks_rank_adjustment?: number;
    final_rank_score?: number;
    market_sentiment_stage?: 'ICE' | 'REPAIR' | 'ADVANCE' | 'CLIMAX' | 'RETREAT' | string;
    market_sentiment_label?: string;
    market_sentiment_score?: number;
    portfolio_position_cap_pct?: number;
    trade_opportunity_score?: number;
    trade_opportunity_label?: string;
    trade_state?: 'BLOCKED' | 'WATCHLIST' | 'PROBE' | 'CONFIRM_ADD' | 'TREND_HOLD' | string;
    execution_instruction?: string;
    execution_rr?: { planned_rr?: number; current_rr?: number; space_rr?: number; execution_rr?: number; price_basis?: number };
    execution_plan_state?: { state?: string; active_confirmation_price?: number; prior_confirmation_price?: number; generated_confirmation_price?: number };
    distance_to_trade?: { remaining_count?: number; steps?: string[]; invalidation_price?: number };
    position_plan?: {
        label: string;
        initial_position_pct: number;
        max_position_pct: number;
        portfolio_position_cap_pct: number;
        tv_execution_risk_unit?: number;
    };
    decision_score_components?: Record<string, number>;
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
    pa_close_guard_price?: number;
    pa_hard_stop_price?: number;
    pa_invalidation_basis?: string;
    pa_invalidation_rule?: string;
    pa_close_confirmation_as_of?: string;
    pa_close_confirmation_phase?: 'INTRADAY_PROVISIONAL' | 'LATE_SESSION' | 'AFTER_CLOSE' | 'LEGACY_UNKNOWN';
    pa_close_time_eligible?: boolean;
    pa_target_price?: number;
    pa_risk_reward?: number;
    pa_tags?: string[];
    pa_pullback_legs?: number;
    pa_pullback_structure?: string;
    pa_pullback_status?: 'WAITING_PULLBACK' | 'PENDING_CONFIRMATION' | 'CONFIRMED' | 'INVALIDATED';
    pa_pullback_status_label?: string;
    pa_pullback_support_price?: number;
    pa_pullback_confirmation_price?: number;
    pa_pullback_invalidation_price?: number;
    pa_pullback_action?: string;
    pa_pullback_validity?: {
        status: string;
        label: string;
        score: number;
        action: string;
        pullback_volume_ratio?: number | null;
        confirmation_volume_ratio?: number | null;
        checks: Array<{ key: string; label: string; passed: boolean }>;
    };
    pa_breakout_quality?: string;
    pa_failure_risk?: number;
    pa_entry_quality_score?: number;
    pa_h2_quality?: string;
    pa_h2_state?: string;
    pa_l2_state?: string;
    pa_second_entry_retracement_quality?: string;
    pa_follow_through_state?: string;
    pa_range_rule?: string;
    pa_failed_breakout_type?: string | null;
    pa_trap_risk?: number;
    pa_micro_channel?: string;
    pa_always_in_strength?: number;
    pa_trend_damage?: string;
    pa_channel_state?: string;
    pa_position_strategy?: string;
    pa_weekly_context?: string;
    pa_monthly_trend?: string;
    pa_monthly_state?: 'UP' | 'REPAIR' | 'DOWN' | 'UNAVAILABLE';
    pa_monthly_as_of?: string | null;
    pa_weekly_position?: string;
    pa_weekly_position_state?: 'PULLBACK' | 'EXTENDED' | 'WEAK' | 'NEUTRAL' | 'UNAVAILABLE';
    pa_weekly_position_as_of?: string | null;
    pa_weekly_pattern_signals?: string[];
    weekly_pattern_watch_only?: boolean;
    pa_swing_entry_route?: 'BREAKOUT' | 'PULLBACK' | 'WAIT';
    pa_timeframe_shadow_only?: boolean;
    pa_multi_timeframe_score?: number;
    pa_multi_timeframe_note?: string;
    pa_volume_pattern?: string;
    pa_volume_confirmed?: boolean;
    pa_volume_pullback_status?: 'NONE' | 'BREAKOUT' | 'WAITING_PULLBACK' | 'PULLBACK' | 'CONFIRMED' | 'UNQUALIFIED' | 'INVALIDATED';
    pa_volume_pullback_label?: string;
    pa_volume_pullback_score_delta?: number;
    pa_volume_pullback_breakout_date?: string | null;
    pa_volume_pullback_support_price?: number | null;
    pa_volume_pullback_confirmation_label?: string | null;
    pa_volume_pullback_confirmation_date?: string | null;
    pa_volume_pullback_stop_price?: number | null;
    pa_volume_risk?: string;
    pa_failed_second_entry?: string | null;
    pa_second_entry_risk?: number;
    pa_gap_type?: string;
    pa_gap_type_v2?: string;
    pa_gap_fill_pct?: number;
    pa_opening_behavior?: string;
    pa_gap_risk?: number;
    pa_range_width_quality?: string;
    pa_range_center_risk?: number;
    pa_range_failed_breakout_count?: number;
    pa_trend_phase?: string;
    pa_trend_phase_action?: string;
    pa_structure_state?: string;
    pa_structure_state_label?: string;
    pa_structure_state_action?: string;
    pa_mtr_state?: string;
    pa_mtr_direction?: string;
    pa_sr_confluence_grade?: string;
    pa_mtf_state?: string;
    pa_decision_summary?: string;
    pa_eight_rule_primary?: {
        rule_id: string;
        rule_code: string;
        label: string;
        direction: 'RISK' | 'BULLISH';
        confidence: number;
        score_delta: number;
        action: string;
        trigger_price?: number | null;
        invalidation_price?: number | null;
        note: string;
    } | null;
    pa_trade_action?: 'READY' | 'WATCH' | 'WAIT' | 'AVOID' | string;
    pa_execution_stage?: 'OBSERVATION_ONLY' | 'BLOCKED' | 'INTRADAY_PREVIEW' | 'SETUP_READY' | 'EOD_CONFIRMED' | 'NEXT_SESSION_REVIEW' | 'NEXT_SESSION_EXECUTABLE' | 'WAITING_SETUP' | string;
    pa_execution_stage_label?: string;
    pa_signal_date?: string;
    pa_volume_confirmation_state?: 'PROVISIONAL' | 'CONFIRMED' | 'NOT_CONFIRMED' | string;
    frozen_plan_date?: string;
    frozen_plan_expiry_date?: string;
    active_confirmation_price?: number;
    active_stop_price?: number;
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
        close_guard_price?: number;
        hard_stop_price?: number;
        invalidation_basis?: string;
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

export type ScanStrategyType = 'tv_dual_strict' | 'tv_dual' | 'early_value' | 'bottom_discovery' | 'weekly_four_patterns' | 'sector_watch' | 'squeeze' | 'pine' | 'both' | 'consensus' | 'tv_zp' | 'h2' | 'high_tight_flag' | 'turtle_breakout' | 'limit_up_shakeout' | 'ma_volume' | 'uptrend_limit_down' | 'rps_breakout' | 'trader_vic_2b';

export interface ScanParams {
    strategy_type: ScanStrategyType;
    strategy_types: ScanStrategyType[];
    match_mode: 'any' | 'all';
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

export interface ScanCompletionSummary {
    taskId: string | null;
    strategyType: ScanParams['strategy_type'];
    strategyTypes: ScanStrategyType[];
    matchMode: ScanParams['match_mode'];
    strategyLabel: string;
    count: number;
    revivalCount: number;
    momentumCount: number;
    excludedCount: number;
    completedAt: string;
    dataDate: string | null;
    dataMode: string | null;
    asOf: string | null;
}

export type ScanResultGroupKey = 'formal' | 'revival' | 'momentum';

export interface ScanResultGroups {
    formal: ScanResult[];
    revival: ScanResult[];
    momentum: ScanResult[];
}

const SCAN_STRATEGY_LABELS: Record<ScanParams['strategy_type'], string> = {
    tv_dual_strict: 'TV+ 强确认精选',
    tv_dual: 'TV 宽松观察池',
    early_value: '早期性价比',
    bottom_discovery: 'B0 底部起涨发现',
    weekly_four_patterns: '周线四形态观察',
    sector_watch: '板块观察',
    squeeze: '均线粘合',
    pine: 'Pine 多指标',
    both: '双重共振',
    consensus: '放量突破',
    tv_zp: 'TV-ZP',
    h2: 'H2 二次入场',
    high_tight_flag: '高窄旗形（HTF，SHADOW）',
    turtle_breakout: '海龟突破（20日新高，SHADOW）',
    limit_up_shakeout: '涨停后洗盘（SHADOW）',
    ma_volume: '均线放量金叉（SHADOW）',
    uptrend_limit_down: '上升趋势放量跌停（SHADOW）',
    rps_breakout: 'RPS 强势突破（SHADOW）',
    trader_vic_2b: 'Trader Vic 2B反转（SHADOW）',
};

let resultRequestGeneration = 0;

function selectedStrategies(params: ScanParams): ScanStrategyType[] {
    return params.strategy_types?.length ? params.strategy_types : [params.strategy_type];
}

function filterStrategyResults(rawResults: unknown, strategyTypes: ScanStrategyType[], matchMode: ScanParams['match_mode']): ScanResult[] {
    const received = Array.isArray(rawResults) ? rawResults : [];
    const selected = new Set(strategyTypes);
    const byCode = new Map<string, ScanResult>();
    received.forEach((item) => {
        if (!item || typeof item !== 'object') return;
        const candidate = item as Partial<ScanResult>;
        const matches = Array.from(new Set([
            ...(candidate.matched_strategies || []),
            ...(candidate.strategy_type ? [candidate.strategy_type] : []),
        ])).filter(strategy => selected.has(strategy as ScanStrategyType));
        if (matches.length === 0 || !candidate.代码) return;
        const code = String(candidate.代码).padStart(6, '0');
        const existing = byCode.get(code);
        if (existing) {
            const matchedStrategies = Array.from(new Set([
                ...(existing.matched_strategies || []),
                ...matches,
            ]));
            const existingRank = Number(existing.display_trade_score ?? existing.Score ?? 0);
            const candidateRank = Number(candidate.display_trade_score ?? candidate.Score ?? 0);
            if (candidateRank > existingRank) {
                byCode.set(code, { ...candidate, matched_strategies: matchedStrategies } as ScanResult);
            } else {
                existing.matched_strategies = matchedStrategies;
            }
            return;
        }
        byCode.set(code, { ...candidate, matched_strategies: matches } as ScanResult);
    });
    const merged = Array.from(byCode.values());
    return matchMode === 'all'
        ? merged.filter(item => strategyTypes.every(strategy => item.matched_strategies?.includes(strategy)))
        : merged;
}

function groupStrategyResults(rawResults: unknown, strategyTypes: ScanStrategyType[], matchMode: ScanParams['match_mode']): ScanResultGroups {
    const matched = filterStrategyResults(rawResults, strategyTypes, matchMode);
    return matched.reduce<ScanResultGroups>((groups, candidate) => {
        if (candidate.result_group === 'FORMAL' || candidate.result_group === 'SHADOW_RESEARCH') {
            groups.formal.push(candidate);
            return groups;
        }
        if (candidate.result_group === 'HISTORICAL_REVIVAL') {
            groups.revival.push(candidate);
            return groups;
        }
        if (candidate.result_group === 'MOMENTUM_WATCH') {
            groups.momentum.push(candidate);
            return groups;
        }
        const checks = candidate.sop_checks || [];
        const bonuses = candidate.sop_bonuses || [];
        const isRevival = candidate.revival_watch_only
            || checks.some(item => item.includes('复活'))
            || bonuses.some(item => item.includes('历史信号复活'));
        const isMomentum = candidate.momentum_acceleration_watch_only
            || checks.some(item => item.includes('强趋势加速'))
            || bonuses.some(item => item.includes('涨停/大阳加速观察'));
        if (isRevival) {
            groups.revival.push(candidate);
        } else if (isMomentum) {
            groups.momentum.push(candidate);
        } else {
            groups.formal.push(candidate);
        }
        return groups;
    }, { formal: [], revival: [], momentum: [] });
}

function buildScanCompletion(
    rawResults: unknown,
    strategyTypes: ScanStrategyType[],
    matchMode: ScanParams['match_mode'],
    taskId: string | null,
    scanMeta?: { data_date?: string; data_mode?: string; as_of?: string },
): { results: ScanResult[]; groups: ScanResultGroups; summary: ScanCompletionSummary } {
    const received = Array.isArray(rawResults) ? rawResults : [];
    const groups = groupStrategyResults(received, strategyTypes, matchMode);
    const matchedCount = groups.formal.length + groups.revival.length + groups.momentum.length;
    const metadata = [...groups.formal, ...groups.revival, ...groups.momentum][0];
    const strategyType = strategyTypes[0];

    return {
        results: groups.formal,
        groups,
        summary: {
            taskId,
            strategyType,
            strategyTypes,
            matchMode,
            strategyLabel: strategyTypes.map(item => SCAN_STRATEGY_LABELS[item]).join(matchMode === 'all' ? ' 且 ' : ' 或 '),
            count: groups.formal.length,
            revivalCount: groups.revival.length,
            momentumCount: groups.momentum.length,
            excludedCount: received.length - matchedCount,
            completedAt: new Date().toISOString(),
            dataDate: scanMeta?.data_date || metadata?.data_date || metadata?.date || metadata?.日期 || null,
            dataMode: scanMeta?.data_mode || metadata?.data_mode || null,
            asOf: scanMeta?.as_of || metadata?.as_of || null,
        },
    };
}

interface ScanStore {
    results: ScanResult[];
    resultGroups: ScanResultGroups;
    activeResultGroup: ScanResultGroupKey;
    resultStrategyType: ScanParams['strategy_type'] | null;
    isScanning: boolean;
    selectedStock: ScanResult | null;
    params: ScanParams;
    historyDates: string[];
    selectedDate: string;
    availableDates: Array<{ date: string; stock_count: number }>;
    scanProgress: ScanProgress | null;
    lastScanSummary: ScanCompletionSummary | null;
    viewMode: 'list' | 'grid' | 'history';
    isFilterOpen: boolean;

    setResults: (results: ScanResult[]) => void;
    setActiveResultGroup: (group: ScanResultGroupKey) => void;
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
    strategy_types: ['tv_dual_strict'],
    match_mode: 'any',
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
    resultGroups: { formal: [], revival: [], momentum: [] },
    activeResultGroup: 'formal',
    resultStrategyType: null,
    isScanning: false,
    selectedStock: null,
    params: DEFAULT_PARAMS,
    historyDates: [],
    selectedDate: '',
    availableDates: [],
    scanProgress: null,
    lastScanSummary: null,
    viewMode: 'list',
    isFilterOpen: false,

    setResults: (results) => set({ results }),
    setActiveResultGroup: (activeResultGroup) => set(state => ({
        activeResultGroup,
        results: state.resultGroups[activeResultGroup],
        selectedStock: null,
    })),
    setIsScanning: (isScanning) => set({ isScanning }),
    setSelectedStock: (selectedStock) => set({ selectedStock }),
    setParams: (params) => set({ params }),
    setViewMode: (viewMode) => set({ viewMode }),
    setIsFilterOpen: (isFilterOpen) => set({ isFilterOpen }),
    setSelectedDate: (selectedDate) => set({ selectedDate }),

    startScan: async () => {
        if (get().isScanning) return;
        const requestGeneration = ++resultRequestGeneration;
        let ws: WebSocket | null = null;
        const requestedParams = get().params;
        const requestedStrategies = selectedStrategies(requestedParams);
        const requestedStrategy = requestedStrategies[0];
        const strategyLabel = requestedStrategies.map(item => SCAN_STRATEGY_LABELS[item]).join(requestedParams.match_mode === 'all' ? ' 且 ' : ' 或 ');
        set({
            isScanning: true,
            results: [],
            resultGroups: { formal: [], revival: [], momentum: [] },
            activeResultGroup: 'formal',
            resultStrategyType: requestedStrategy,
            selectedStock: null,
            lastScanSummary: null,
            scanProgress: { current: 0, total: 100, matches: 0, elapsed: 0, message: `正在提交${strategyLabel}扫描任务...` },
        });
        const startTime = Date.now();

        try {
            if (requestedParams.match_mode === 'all' && requestedStrategies.length > 1) {
                try {
                    const capability = await api.get('/api/scan/capabilities');
                    if (!capability.data?.match_modes?.includes('all')) throw new Error('unsupported');
                } catch {
                    throw new Error('后端尚未更新，暂不能执行“且”关系扫描，请更新服务后重试。');
                }
            }
            const cleanParams: ScanMarketParams = {
                ...requestedParams,
                strategy_type: requestedStrategy,
                strategy_types: requestedStrategies.join(','),
            };
            Object.keys(cleanParams).forEach(key => {
                const typedKey = key as keyof ScanMarketParams;
                const val = cleanParams[typedKey];
                if (typeof val === 'number' && isNaN(val)) {
                    delete cleanParams[typedKey];
                }
            });
            if (typeof cleanParams.data_date !== 'string' || !cleanParams.data_date.trim()) {
                delete cleanParams.data_date;
            }

            ws = connectScanWebSocket((msg) => {
                if (requestGeneration !== resultRequestGeneration) return;
                const elapsed = Math.floor((Date.now() - startTime) / 1000);
                if (msg.type === 'scan_start') {
                    set({ scanProgress: { current: 0, total: 100, matches: 0, elapsed, message: msg.message } });
                } else if (msg.type === 'scan_progress') {
                    set({ scanProgress: { current: msg.current ?? 0, total: msg.total ?? 0, matches: 0, elapsed, message: msg.message } });
                } else if (msg.type === 'scan_end') {
                    set({ scanProgress: { current: 100, total: 100, matches: 0, elapsed, message: `正在核对${strategyLabel}正式入选结果...` } });
                }
            });

            const submitRes = await marketApi.scanMarket(cleanParams);
            
            if (submitRes.data.task_id) {
                const taskId = submitRes.data.task_id;
                let consecutivePollFailures = 0;
                
                // Poll for status
                return new Promise<void>((resolve) => {
                    const pollInterval = setInterval(async () => {
                        if (requestGeneration !== resultRequestGeneration) {
                            clearInterval(pollInterval);
                            if (ws) { try { ws.close(); } catch {} }
                            resolve();
                            return;
                        }
                        try {
                            const statusRes = await api.get(`/api/scan/status/${taskId}`);
                            if (requestGeneration !== resultRequestGeneration) {
                                clearInterval(pollInterval);
                                if (ws) { try { ws.close(); } catch {} }
                                resolve();
                                return;
                            }
                            consecutivePollFailures = 0;
                            const state = statusRes.data.status;
                            
                            if (state === 'SUCCESS') {
                                clearInterval(pollInterval);
                                // Allow progress animation to complete
                                setTimeout(() => {
                                    if (requestGeneration !== resultRequestGeneration) {
                                        if (ws) { try { ws.close(); } catch {} }
                                        resolve();
                                        return;
                                    }
                                    const completion = buildScanCompletion(
                                        statusRes.data.results,
                                        requestedStrategies,
                                        requestedParams.match_mode,
                                        taskId,
                                        statusRes.data.scan_meta,
                                    );
                                    set({
                                        results: completion.results,
                                        resultGroups: completion.groups,
                                        activeResultGroup: 'formal',
                                        resultStrategyType: requestedStrategy,
                                        lastScanSummary: completion.summary,
                                        selectedDate: completion.summary.dataDate || '',
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
                            if (requestGeneration !== resultRequestGeneration) {
                                clearInterval(pollInterval);
                                if (ws) { try { ws.close(); } catch {} }
                                resolve();
                                return;
                            }
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
                    if (requestGeneration !== resultRequestGeneration) {
                        if (ws) { try { ws.close(); } catch {} }
                        return;
                    }
                    const rawResults = submitRes.data.results || submitRes.data || [];
                    const completion = buildScanCompletion(
                        rawResults,
                        requestedStrategies,
                        requestedParams.match_mode,
                        null,
                        submitRes.data.scan_meta,
                    );
                    set({
                        results: completion.results,
                        resultGroups: completion.groups,
                        activeResultGroup: 'formal',
                        resultStrategyType: requestedStrategy,
                        lastScanSummary: completion.summary,
                        selectedDate: completion.summary.dataDate || '',
                        isScanning: false,
                        scanProgress: null,
                    });
                    get().fetchHistory();
                    if (ws) { try { ws.close(); } catch {} }
                }, 1000);
            }
        } catch (e: unknown) {
            if (requestGeneration !== resultRequestGeneration) {
                if (ws) { try { ws.close(); } catch {} }
                return;
            }
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
        if (get().isScanning) return;
        const requestGeneration = ++resultRequestGeneration;
        const strategyTypes = selectedStrategies(get().params);
        const matchMode = get().params.match_mode;
        const strategyKey = strategyTypes.join(',');
        const strategyType = strategyTypes[0];
        set({ selectedDate: date, isScanning: true, lastScanSummary: null });
        if (!date) {
            set({ isScanning: false });
            return;
        }
        try {
            const res = await api.get(`/api/scan/history?date=${date}`);
            if (
                requestGeneration === resultRequestGeneration
                && get().selectedDate === date
                && selectedStrategies(get().params).join(',') === strategyKey
                && get().params.match_mode === matchMode
            ) {
                const groups = groupStrategyResults(res.data, strategyTypes, matchMode);
                set({ results: groups.formal, resultGroups: groups, activeResultGroup: 'formal', resultStrategyType: strategyType });
            }
        } catch (e) {
            console.error("Failed to load history", e);
        } finally {
            if (requestGeneration === resultRequestGeneration) set({ isScanning: false });
        }
    },

    fetchHistory: async () => {
        const requestGeneration = ++resultRequestGeneration;
        const strategyTypes = selectedStrategies(get().params);
        const matchMode = get().params.match_mode;
        const strategyKey = strategyTypes.join(',');
        const strategyType = strategyTypes[0];
        try {
            const dateRes = await api.get('/api/scan/dates');
            const dates = dateRes.data;
            set({ historyDates: dates });

            // If we have history and no current results, load latest
            if (
                requestGeneration === resultRequestGeneration
                && selectedStrategies(get().params).join(',') === strategyKey
                && get().params.match_mode === matchMode
                && dates.length > 0
                && get().results.length === 0
                && !get().isScanning
                && !get().lastScanSummary
            ) {
                const latestDate = dates[0];
                set({ selectedDate: latestDate });
                const res = await api.get(`/api/scan/history?date=${latestDate}`);
                if (
                    requestGeneration === resultRequestGeneration
                    && get().selectedDate === latestDate
                    && selectedStrategies(get().params).join(',') === strategyKey
                    && get().params.match_mode === matchMode
                    && !get().isScanning
                    && !get().lastScanSummary
                ) {
                    const groups = groupStrategyResults(res.data, strategyTypes, matchMode);
                    set({ results: groups.formal, resultGroups: groups, activeResultGroup: 'formal', resultStrategyType: strategyType });
                }
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
        const csvContent = toCsvString(headers, rows);
        downloadCsv(`AlphaVision_Results_${new Date().toLocaleDateString()}.csv`, csvContent);
    },
}));
