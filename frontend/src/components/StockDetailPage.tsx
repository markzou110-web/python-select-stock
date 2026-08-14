"use client";

import React, { useCallback, useEffect, useState } from 'react';
import {
    ArrowLeft,
    TrendingUp,
    TrendingDown,
    ShieldCheck,
    AlertTriangle,
    Activity,
    Target,
    BarChart3,
    Zap,
    Brain,
    Loader2,
    Layers,
    DollarSign,
    ShieldAlert,
    Gauge,
    Clock,
    Tag,
    PieChart as PieIcon,
    CheckCircle2,
    XCircle,
    MinusCircle,
    PlusCircle,
    Star,
    RefreshCw,
    FileText,
    Newspaper,
    ExternalLink,
    BookOpen,
    Save
} from 'lucide-react';
import { clampScore, cn } from '@/lib/utils';
import api from '@/lib/api';
import SplitKLineCharts from './SplitKLineCharts';

interface StockDetailPageProps {
    code: string;
    name: string;
    onBack: () => void;
}

interface FullAnalysisData {
    code: string;
    kline: any[];
    signals: any;
    stock_info: any;
    concepts: { name: string; pct: number }[];
    financials: {
        roe: number | null;
        pe_ttm: number | null;
        pe_percentile: string;
        net_profit_yoy: number | null;
        revenue_yoy: number | null;
        label: string;
        mkt_cap_yi: number | null;
    };
    risk_assessment: {
        volatility: string;
        liquidity: string;
        sector_risk: string;
        market_regime: string;
        warnings: string[];
        risk_level: string;
    };
    ai_suggestion: {
        action: string;
        confidence: number;
        reasoning: string[];
        action_label: string;
    };
    price_action?: any;
    price_action_lines?: any[];
    money_flow?: MoneyFlowData;
}

interface MoneyFlowData {
    status?: string;
    source?: string;
    cache_hit?: boolean;
    updated_at?: string;
    flow_metric?: string;
    metric_label?: string;
    supports_order_breakdown?: boolean;
    latest?: {
        main_net_inflow_yi?: number;
        main_net_ratio?: number;
        super_net_inflow_yi?: number;
        big_net_inflow_yi?: number;
    };
    summary?: {
        bias?: 'inflow' | 'outflow' | 'neutral' | string;
        main_net_5d_yi?: number;
        consecutive_direction?: 'inflow' | 'outflow' | 'flat' | string;
        consecutive_days?: number;
    };
}

interface StockResearchData {
    status?: 'ok' | 'partial' | string;
    cache_hit?: boolean;
    updated_at?: string;
    errors?: Record<string, string>;
    summary?: {
        label?: string;
        risk_flags?: string[];
        opportunity_flags?: string[];
    };
    announcements?: Array<{ title?: string; date?: string; type?: string; url?: string }>;
    news?: Array<{ title?: string; time?: string; source?: string; url?: string }>;
    reports?: Array<{ title?: string; publish_date?: string; org?: string; rating?: string }>;
    lockup?: { upcoming?: Array<{ date?: string; ratio?: number }> };
    holder_count?: Array<{ date?: string; change_ratio?: number }>;
    evidence_quality?: {
        grade?: string;
        status?: string;
        summary?: string;
        reason_codes?: string[];
    };
    decision_memo?: {
        bull_case?: Array<{ text?: string } | string>;
        bear_case?: Array<{ text?: string } | string>;
        unknowns?: string[];
        invalidation_conditions?: string[];
    };
}

export default function StockDetailPage({ code, name, onBack }: StockDetailPageProps) {
    const [data, setData] = useState<FullAnalysisData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [addingAction, setAddingAction] = useState<'paper' | 'watchlist' | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [research, setResearch] = useState<StockResearchData | null>(null);
    const [researchLoading, setResearchLoading] = useState(false);

    const showToast = useCallback((message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        window.setTimeout(() => setToast(null), 2600);
    }, []);

    const fetchData = useCallback(async (showLoading = false) => {
        if (showLoading) {
            setLoading(true);
        }
        setError(null);
        try {
            const res = await api.get(`/api/stock/full-analysis?code=${code}`);
            const analysis = res.data;
            try {
                const [squeezeRes, tvRes] = await Promise.all([
                    api.get(`/api/stock/${code}/signals?strategy=squeeze`),
                    api.get(`/api/stock/${code}/signals?strategy=tv_zp`),
                ]);
                analysis.signals = {
                    ...(analysis.signals || {}),
                    strategy_sets: {
                        ...(analysis.signals?.strategy_sets || {}),
                        squeeze: {
                            ...(squeezeRes.data || {}),
                            strategy_type: 'squeeze',
                            buy_count: squeezeRes.data?.buy_signals?.length || 0,
                            sell_count: squeezeRes.data?.sell_signals?.length || 0,
                        },
                        tv_zp: {
                            ...(tvRes.data || {}),
                            strategy_type: 'tv_zp',
                            buy_count: tvRes.data?.buy_signals?.length || 0,
                            sell_count: tvRes.data?.sell_signals?.length || 0,
                        },
                    },
                };
            } catch (signalErr) {
                console.warn("Overlay signal fetch failed:", signalErr);
            }
            setData(analysis);
        } catch (err: any) {
            console.error("Full analysis fetch error:", err);
            if (showLoading) {
                setError(err.response?.data?.detail || '数据加载失败');
            }
        } finally {
            setLoading(false);
        }
    }, [code]);

    const fetchResearch = useCallback(async (forceRefresh = false) => {
        setResearchLoading(true);
        try {
            const res = await api.get(`/api/stock/${code}/research`, {
                params: { force_refresh: forceRefresh },
            });
            setResearch(res.data);
        } catch (researchErr) {
            console.warn('Stock research fetch failed:', researchErr);
        } finally {
            setResearchLoading(false);
        }
    }, [code]);

    useEffect(() => {
        fetchData(true);
        const timer = window.setInterval(() => fetchData(false), 30000);
        return () => window.clearInterval(timer);
    }, [fetchData]);

    useEffect(() => {
        setResearch(null);
        fetchResearch(false);
    }, [fetchResearch]);

    if (loading) {
        return (
            <div className="flex-1 flex flex-col items-center justify-center p-20 text-slate-400 gap-4 animate-pulse">
                <div className="w-16 h-16 bg-indigo-50 text-indigo-500 rounded-full flex items-center justify-center shadow-xl shadow-indigo-100">
                    <Brain size={32} />
                </div>
                <p className="font-bold text-lg text-slate-600">正在深度分析 {name}...</p>
                <p className="text-sm font-medium">载入K线、概念、财务、风控数据</p>
                <Loader2 className="animate-spin text-indigo-400" size={24} />
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="flex-1 flex flex-col items-center justify-center p-20 gap-4">
                <AlertTriangle size={48} className="text-amber-400" />
                <p className="font-bold text-lg text-slate-700">分析加载失败</p>
                <p className="text-sm text-slate-400">{error}</p>
                <button onClick={onBack} className="px-6 py-2 bg-indigo-600 text-white rounded-xl font-bold text-sm hover:bg-indigo-700 transition-all">
                    返回列表
                </button>
            </div>
        );
    }

    const info = data.stock_info;
    const suggestion = data.ai_suggestion;
    const risk = data.risk_assessment;
    const fin = data.financials;
    const moneyFlow = data.money_flow || {};
    const moneyLatest = moneyFlow.latest || {};
    const moneySummary = moneyFlow.summary || {};
    const moneyBias = moneySummary.bias || 'neutral';
    const isMainMoneyFlow = moneyFlow.flow_metric === 'main_net_inflow'
        || (!moneyFlow.flow_metric && !String(moneyFlow.source || '').startsWith('ths_'));
    const moneyDirectionLabel = isMainMoneyFlow ? '主力资金' : '资金净额';
    const activeStopPrice = info.active_stop_price || info.stop_price || 0;
    const chartStrategy = data.signals?.strategy_type || info.chart_strategy_type || info.strategy_type || 'squeeze';
    const strategyLabels: Record<string, string> = {
        squeeze: '均线粘合（单策略）',
        pine: '五指标投票共振',
        tv_zp: 'TV-ZP趋势信号',
        consensus: 'Azul 共识',
        both: '均线 + 五指标共振',
    };
    const buySignalCount = data.signals?.buy_count ?? data.signals?.buy_signals?.length ?? 0;
    const sellSignalCount = data.signals?.sell_count ?? data.signals?.sell_signals?.length ?? 0;
    const priceSourceLabel = info.price_source === 'realtime_snapshot'
        ? '实时快照'
        : info.price_source === 'paper_cached_price'
            ? '持仓缓存价'
            : '日线收盘价';
    const paExecutionStage = info.pa_execution_stage || (
        data.price_action?.pa_trade_plan?.action === 'READY' ? 'SETUP_READY' : 'WAITING_SETUP'
    );
    const paStageLabels: Record<string, string> = {
        OBSERVATION_ONLY: '观察策略｜需生成新计划',
        BLOCKED: '结构失效｜禁止买入',
        INTRADAY_PREVIEW: '盘中预观察｜14:30后确认',
        SETUP_READY: '结构就绪｜等待价量确认',
        EOD_CONFIRMED: '尾盘已确认｜次一交易日复核',
        NEXT_SESSION_REVIEW: '次日复核｜尚不可执行',
        NEXT_SESSION_EXECUTABLE: '次日确认｜可执行候选',
        WAITING_SETUP: '等待新结构',
    };
    const paStageLabel = info.pa_execution_stage_label || paStageLabels[paExecutionStage] || paExecutionStage;
    const paPlanDate = info.frozen_plan_date || info.pa_signal_date || info.latest_scan_date || data.kline?.[data.kline.length - 1]?.time;
    const paPlanExpiry = info.frozen_plan_expiry_date;
    const paConfirmationPrice = info.active_confirmation_price || data.price_action?.pa_entry_price;
    const paInvalidationPrice = info.active_stop_price || data.price_action?.pa_stop_price;
    const paNextStep = info.distance_to_trade?.steps?.[0] || (
        paExecutionStage === 'NEXT_SESSION_EXECUTABLE' ? '按仓位与风险预算小仓复核' : '等待系统完成下一阶段确认'
    );

    const actionColors: Record<string, { bg: string; text: string; border: string; glow: string }> = {
        ADD: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200', glow: 'shadow-emerald-100' },
        HOLD: { bg: 'bg-slate-50', text: 'text-slate-700', border: 'border-slate-200', glow: 'shadow-slate-100' },
        REDUCE: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200', glow: 'shadow-amber-100' },
        CLOSE: { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200', glow: 'shadow-rose-100' },
    };
    const actionStyle = actionColors[suggestion.action] || actionColors.HOLD;

    const ActionIcon = suggestion.action === 'ADD' ? PlusCircle :
                       suggestion.action === 'REDUCE' ? MinusCircle :
                       suggestion.action === 'CLOSE' ? XCircle : CheckCircle2;

    const riskColors: Record<string, string> = {
        '低': 'text-emerald-600 bg-emerald-50 border-emerald-100',
        '中': 'text-amber-600 bg-amber-50 border-amber-100',
        '高': 'text-rose-600 bg-rose-50 border-rose-100',
    };

    const regimeLabels: Record<string, { label: string; color: string }> = {
        'OFFENSIVE': { label: '🚀 进攻模式', color: 'text-emerald-600 bg-emerald-50' },
        'DEFENSIVE': { label: '⚠️ 防守观望', color: 'text-amber-600 bg-amber-50' },
        'CRITICAL': { label: '🛡️ 严格防守', color: 'text-rose-600 bg-rose-50' },
    };

    const addToPaperTrade = async (force = false) => {
        setAddingAction('paper');
        try {
            const plan = data.price_action?.pa_trade_plan || {};
            const res = await api.post('/api/paper/add', {
                code: info.代码,
                name: info.名称,
                price: info.current_price || info.现价,
                strategy_type: chartStrategy,
                remark: plan.entry_condition || '详情页加入模拟盘',
                force,
                trade_mode: 'SIMULATED',
                entry_source: info.price_source === 'realtime_snapshot' ? 'detail_realtime_snapshot' : 'detail_current_price',
                entry_signal_date: data.kline?.[data.kline.length - 1]?.time,
                signal_sources: data.signals?.signal_sources || info.signal_sources,
                entry_reason_snapshot: `${plan.setup || data.price_action?.pa_trade_setup || '详情页分析'} / ${plan.action_label || suggestion.action_label || '未分级'} / Score ${info.Score ?? '--'}`,
                pa_trade_action: plan.action || data.price_action?.pa_trade_action,
                pa_trade_setup: plan.setup || data.price_action?.pa_trade_setup,
                pa_entry_condition: plan.entry_condition,
                pa_invalidation: plan.invalidation || data.price_action?.pa_invalidation,
                pa_risk_pct: plan.risk_pct || data.price_action?.pa_risk_pct,
            });
            if (res.data?.status === 'warning') {
                if (window.confirm(res.data.detail || '组合风险预算触发，是否继续加入模拟盘？')) {
                    await addToPaperTrade(true);
                }
                return;
            }
            if (!['success', 'upgraded'].includes(res.data?.status)) {
                showToast(res.data?.detail || '加入模拟盘失败', 'error');
                return;
            }
            showToast(
                res.data.status === 'upgraded'
                    ? `${info.名称} 信号已升级，未重复加仓`
                    : `${info.名称} 已加入模拟盘`,
            );
            await fetchData(false);
        } catch (err: any) {
            console.error("Add to paper trade error:", err);
            showToast(err.response?.data?.detail || '加入模拟盘失败', 'error');
        } finally {
            setAddingAction(null);
        }
    };

    const addToWatchlist = async () => {
        setAddingAction('watchlist');
        try {
            const plan = data.price_action?.pa_trade_plan || {};
            await api.post('/api/watchlist/add', {
                code: info.代码,
                name: info.名称,
                industry: info.行业,
                watch_price: info.current_price || info.现价,
                target_price: data.price_action?.pa_target_price || info.take_profit_price,
                stop_price: data.price_action?.pa_stop_price || info.active_stop_price || info.stop_price,
                strategy_type: chartStrategy,
                reason: plan.entry_condition || `${strategyLabels[chartStrategy] || chartStrategy} 详情页加入观察池，Score ${info.Score ?? '--'}`,
                invalidation: plan.invalidation || data.price_action?.pa_invalidation || '跌破关键风控线或策略结构失效',
                source: 'stock_detail',
                pa_trade_action: plan.action || data.price_action?.pa_trade_action,
                pa_trade_setup: plan.setup || data.price_action?.pa_trade_setup,
                pa_entry_condition: plan.entry_condition,
                pa_invalidation: plan.invalidation || data.price_action?.pa_invalidation,
                pa_risk_pct: plan.risk_pct || data.price_action?.pa_risk_pct,
            });
            showToast(`${info.名称} 已加入观察池`);
        } catch (err: any) {
            console.error("Add to watchlist error:", err);
            showToast(err.response?.data?.detail || '加入观察池失败', 'error');
        } finally {
            setAddingAction(null);
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* ═══ Header Bar ═══ */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <button
                        onClick={onBack}
                        className="p-2.5 bg-white border border-slate-200 rounded-xl hover:bg-slate-50 transition-all text-slate-500 hover:text-indigo-600 shadow-sm"
                    >
                        <ArrowLeft size={20} />
                    </button>
                    <div>
                        <div className="flex items-center gap-3">
                            <h1 className="text-2xl font-black text-slate-900">{info.名称}</h1>
                            <span className="text-sm font-mono font-bold text-slate-400 bg-slate-100 px-2.5 py-0.5 rounded-lg border border-slate-200">{info.代码}</span>
                            <span className="text-xs font-bold px-2.5 py-1 rounded-lg bg-indigo-50 text-indigo-600 border border-indigo-100">{info.行业}</span>
                        </div>
                        <div className="flex items-center gap-4 mt-1">
                            <span className="text-xl font-black font-mono text-slate-800">¥{info.现价}</span>
                            <span
                                className={cn(
                                    "text-[10px] font-bold px-2 py-0.5 rounded-full border inline-flex items-center gap-1",
                                    info.price_source === 'realtime_snapshot'
                                        ? "text-emerald-600 bg-emerald-50 border-emerald-200"
                                        : "text-slate-400 bg-slate-50 border-slate-200"
                                )}
                                title={`价格来源：${priceSourceLabel}${info.price_updated_at ? ` · ${info.price_updated_at}` : ''}`}
                            >
                                {info.price_source === 'realtime_snapshot' && (
                                    <span className="relative flex h-1.5 w-1.5">
                                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
                                    </span>
                                )}
                                {info.price_source === 'realtime_snapshot'
                                    ? `实时${info.price_updated_at ? ' ' + info.price_updated_at.slice(11, 19) : ''}`
                                    : '收盘价'}
                            </span>
                            <span className={cn(
                                "text-sm font-black px-2 py-0.5 rounded-lg",
                                info['涨幅%'] >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"
                            )}>
                                {info['涨幅%'] >= 0 ? '+' : ''}{info['涨幅%']}%
                            </span>
                            <span className="text-xs font-bold text-slate-400">信号强度 {clampScore(info.display_signal_score ?? info.Score).toFixed(1)}/100</span>
                            <span className="text-xs font-bold text-slate-400">RSI {info.RSI}</span>
                        </div>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {info.is_paper_trade ? (
                        <div className="flex items-center gap-2 px-4 py-2 bg-indigo-50 border border-indigo-200 rounded-2xl shadow-sm">
                            <div className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                            <span className="text-xs font-black text-indigo-600 uppercase tracking-widest">拟合实盘持仓中</span>
                        </div>
                    ) : (
                        <button
                            onClick={() => addToPaperTrade(false)}
                            disabled={addingAction !== null}
                            className="px-4 py-2 rounded-2xl bg-indigo-600 text-white text-xs font-black shadow-lg shadow-indigo-100 hover:bg-indigo-700 disabled:opacity-50 transition-all flex items-center gap-2"
                        >
                            {addingAction === 'paper' ? <Loader2 size={14} className="animate-spin" /> : <PlusCircle size={14} />}
                            加入模拟盘
                        </button>
                    )}
                    <button
                        onClick={addToWatchlist}
                        disabled={addingAction !== null}
                        className="px-4 py-2 rounded-2xl bg-white border border-slate-200 text-slate-700 text-xs font-black shadow-sm hover:bg-slate-50 disabled:opacity-50 transition-all flex items-center gap-2"
                    >
                        {addingAction === 'watchlist' ? <Loader2 size={14} className="animate-spin" /> : <Star size={14} />}
                        加入观察池
                    </button>
                </div>
            </div>

            {/* ═══ Split K-Line / Price Action Charts ═══ */}
            <div className="glass-card overflow-hidden">
                {/* Chart legend */}
                <div className="px-5 py-3 bg-slate-50/50 border-b border-slate-100 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <div className="flex items-center gap-1.5">
                            <div className="w-2.5 h-2.5 rounded-full bg-indigo-500" />
                            <span className="text-[10px] font-bold text-slate-500">EMA5</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <div className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                            <span className="text-[10px] font-bold text-slate-500">EMA20</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <div className="w-2.5 h-2.5 rounded-full bg-purple-500" />
                            <span className="text-[10px] font-bold text-slate-500">EMA60</span>
                        </div>
                        {info.is_paper_trade && (
                            <>
                                <div className="w-px h-4 bg-slate-200" />
                                <div className="flex items-center gap-1.5">
                                    <div className="w-3 h-0.5 bg-indigo-500 border-t border-dashed border-indigo-500" />
                                    <span className="text-[10px] font-bold text-indigo-500">买入价</span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <div className="w-3 h-0.5 bg-rose-500 border-t border-dashed border-rose-500" />
                                    <span className="text-[10px] font-bold text-rose-500">实时持仓风控线</span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <div className="w-3 h-0.5 bg-emerald-500 border-t border-dashed border-emerald-500" />
                                    <span className="text-[10px] font-bold text-emerald-500">止盈价</span>
                                </div>
                            </>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="rounded-md bg-blue-50 px-2 py-1 text-[10px] font-black text-blue-700 border border-blue-100">
                            {strategyLabels[chartStrategy] || chartStrategy}
                        </span>
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">
                            买点 {buySignalCount} · 卖点 {sellSignalCount}
                        </span>
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">200日K线 / 价格行为</span>
                    </div>
                </div>
                <SplitKLineCharts
                    candles={data.kline}
                    emaLines={[
                        { key: 'EMA5', label: 'EMA5', color: '#6366f1' },
                        { key: 'EMA20', label: 'EMA20', color: '#f59e0b' },
                        { key: 'EMA60', label: 'EMA60', color: '#8b5cf6' },
                    ]}
                    trailingStops={data.signals?.trailing_stops || []}
                    buySignals={data.signals?.buy_signals || []}
                    sellSignals={data.signals?.sell_signals || []}
                    strategySignalSets={data.signals?.strategy_sets || {}}
                    priceAction={data.price_action}
                    priceActionLines={data.price_action_lines || []}
                    riskLevels={info}
                    paperLines={info.is_paper_trade ? [
                        { price: info.buy_price, label: '买入价', color: '#6366f1', date: info.entry_date },
                        { price: activeStopPrice, label: '实时持仓风控线', color: '#f43f5e' },
                        { price: info.take_profit_price, label: '止盈价', color: '#10b981' },
                    ] : []}
                    height={460}
                />
            </div>

            {data.price_action?.pa_trade_plan && (
                <div className="glass-card p-5 border border-slate-200">
                    <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
                        <div className="space-y-2 flex-1">
                            <div className="flex items-center gap-2">
                                <Target size={16} className="text-blue-500" />
                                <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">Brooks 交易计划</h4>
                                <span className={cn(
                                    "text-[10px] font-black px-2 py-0.5 rounded-lg border",
                                    paExecutionStage === 'NEXT_SESSION_EXECUTABLE' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                        paExecutionStage === 'BLOCKED' ? "bg-rose-50 text-rose-700 border-rose-100" :
                                            "bg-amber-50 text-amber-700 border-amber-100"
                                )}>
                                    {paStageLabel}
                                </span>
                            </div>
                            <div className="grid grid-cols-2 gap-2 rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-[11px] font-bold text-slate-600 md:grid-cols-5">
                                <p><span className="text-slate-400">计划日：</span>{paPlanDate || '--'}</p>
                                <p><span className="text-slate-400">有效至：</span>{paPlanExpiry || '待生成冻结计划'}</p>
                                <p><span className="text-slate-400">确认价：</span>{paConfirmationPrice ? Number(paConfirmationPrice).toFixed(2) : '--'}</p>
                                <p><span className="text-slate-400">失效价：</span>{paInvalidationPrice ? Number(paInvalidationPrice).toFixed(2) : '--'}</p>
                                <p className="col-span-2 md:col-span-1"><span className="text-slate-400">下一步：</span>{paNextStep}</p>
                            </div>
                            <div className="text-base font-black text-slate-800">{data.price_action.pa_trade_plan.setup}</div>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-semibold text-slate-600">
                                <p><span className="text-slate-400">触发：</span>{data.price_action.pa_trade_plan.entry_condition}</p>
                                <p><span className="text-slate-400">失效：</span>{data.price_action.pa_trade_plan.invalidation}</p>
                                <p><span className="text-slate-400">仓位：</span>{data.price_action.pa_trade_plan.position_hint} / 风险 {data.price_action.pa_trade_plan.risk_pct || 0}%</p>
                            </div>
                            <div className="flex flex-wrap gap-1.5 text-[10px] font-bold">
                                {data.price_action.pa_pullback_structure && (
                                    <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full border border-blue-100">
                                        {data.price_action.pa_pullback_structure}
                                        {data.price_action.pa_pullback_legs != null ? ` · ${data.price_action.pa_pullback_legs}腿` : ''}
                                    </span>
                                )}
                                {data.price_action.pa_breakout_quality && (
                                    <span className="px-2 py-0.5 bg-slate-50 text-slate-600 rounded-full border border-slate-100">突破：{data.price_action.pa_breakout_quality}</span>
                                )}
                                {data.price_action.pa_failure_risk != null && (
                                    <span className={cn(
                                        "px-2 py-0.5 rounded-full border",
                                        data.price_action.pa_failure_risk >= 70
                                            ? "bg-rose-50 text-rose-700 border-rose-100"
                                            : "bg-amber-50 text-amber-700 border-amber-100"
                                    )}>
                                        失败风险：{data.price_action.pa_failure_risk}%
                                    </span>
                                )}
                                {data.price_action.pa_h2_quality && data.price_action.pa_h2_quality !== '不适用' && (
                                    <span className="px-2 py-0.5 bg-emerald-50 text-emerald-700 rounded-full border border-emerald-100">
                                        H2质量：{data.price_action.pa_h2_quality}
                                        {data.price_action.pa_entry_quality_score != null ? ` · ${data.price_action.pa_entry_quality_score}分` : ''}
                                    </span>
                                )}
                                {data.price_action.pa_failed_breakout_type && (
                                    <span className="px-2 py-0.5 bg-rose-50 text-rose-700 rounded-full border border-rose-100">
                                        {data.price_action.pa_failed_breakout_type}
                                    </span>
                                )}
                                {data.price_action.pa_range_rule && (
                                    <span className="px-2 py-0.5 bg-slate-50 text-slate-600 rounded-full border border-slate-100">
                                        {data.price_action.pa_range_rule}
                                    </span>
                                )}
                                {data.price_action.pa_micro_channel && data.price_action.pa_micro_channel !== '无' && (
                                    <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full border border-blue-100">
                                        {data.price_action.pa_micro_channel}
                                    </span>
                                )}
                                {data.price_action.pa_always_in_strength != null && data.price_action.pa_always_in_strength > 0 && (
                                    <span className="px-2 py-0.5 bg-emerald-50 text-emerald-700 rounded-full border border-emerald-100">
                                        Always In：{data.price_action.pa_always_in_strength}分
                                    </span>
                                )}
                                {data.price_action.pa_trend_damage && data.price_action.pa_trend_damage !== '无' && (
                                    <span className="px-2 py-0.5 bg-rose-50 text-rose-700 rounded-full border border-rose-100">
                                        {data.price_action.pa_trend_damage}
                                    </span>
                                )}
                                {data.price_action.pa_position_strategy && (
                                    <span className="px-2 py-0.5 bg-slate-50 text-slate-600 rounded-full border border-slate-100">
                                        {data.price_action.pa_position_strategy}
                                    </span>
                                )}
                                {data.price_action.pa_weekly_context && (
                                    <span className="px-2 py-0.5 bg-slate-50 text-slate-600 rounded-full border border-slate-100">
                                        {data.price_action.pa_weekly_context}
                                        {data.price_action.pa_multi_timeframe_score != null ? ` · ${data.price_action.pa_multi_timeframe_score}分` : ''}
                                    </span>
                                )}
                                {data.price_action.pa_volume_pattern && data.price_action.pa_volume_pattern !== '量能中性' && (
                                    <span className={cn(
                                        "px-2 py-0.5 rounded-full border",
                                        data.price_action.pa_volume_confirmed
                                            ? "bg-emerald-50 text-emerald-700 border-emerald-100"
                                            : "bg-amber-50 text-amber-700 border-amber-100"
                                    )}>
                                        {data.price_action.pa_volume_pattern}
                                    </span>
                                )}
                                {data.price_action.pa_gap_type && data.price_action.pa_gap_type !== '无缺口' && (
                                    <span className="px-2 py-0.5 bg-amber-50 text-amber-700 rounded-full border border-amber-100">
                                        {data.price_action.pa_gap_type}
                                    </span>
                                )}
                                {data.price_action.pa_trend_phase && (
                                    <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full border border-blue-100">
                                        {data.price_action.pa_trend_phase}
                                    </span>
                                )}
                            </div>
                            {data.price_action.pa_decision_summary && (
                                <p className="text-xs font-semibold text-slate-500 leading-relaxed">{data.price_action.pa_decision_summary}</p>
                            )}
                        </div>
                        {data.price_action.pa_trade_plan.avoid_reasons?.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 lg:max-w-sm">
                                {data.price_action.pa_trade_plan.avoid_reasons.map((reason: string, i: number) => (
                                    <span key={i} className="text-[10px] px-2 py-0.5 bg-slate-50 text-slate-500 rounded-full font-bold border border-slate-100">{reason}</span>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {(info.position_decision || info.trade_bucket || info.latest_scan_pa_action) && (
                <div className="glass-card p-5 border border-slate-200">
                    <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
                        <div className="flex items-start gap-3">
                            <div className={cn(
                                "w-10 h-10 rounded-xl flex items-center justify-center",
                                info.trade_bucket === 'BLOCK' || info.position_decision?.grade === 'EXIT'
                                    ? "bg-rose-50 text-rose-600"
                                    : info.trade_bucket === 'TRADE' || info.position_decision?.grade?.startsWith('HOLD')
                                        ? "bg-emerald-50 text-emerald-600"
                                        : "bg-amber-50 text-amber-600"
                            )}>
                                {info.trade_bucket === 'BLOCK' || info.position_decision?.grade === 'EXIT' ? <XCircle size={20} /> : <CheckCircle2 size={20} />}
                            </div>
                            <div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">执行纪律</h4>
                                    {info.trade_bucket && (
                                        <span className={cn(
                                            "px-2 py-0.5 rounded-md border text-[10px] font-black",
                                            info.trade_bucket === 'TRADE' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                                info.trade_bucket === 'BLOCK' ? "bg-rose-50 text-rose-700 border-rose-100" :
                                                    "bg-sky-50 text-sky-700 border-sky-100"
                                        )}>
                                            {info.trade_bucket}
                                        </span>
                                    )}
                                    {(info.display_trade_score ?? info.final_trade_score) != null && (
                                        <span className="px-2 py-0.5 rounded-md bg-slate-50 border border-slate-100 text-[10px] font-black text-slate-500">
                                            交易分 {clampScore(info.display_trade_score ?? info.final_trade_score).toFixed(0)}/100
                                        </span>
                                    )}
                                </div>
                                <div className="mt-1 text-base font-black text-slate-800">
                                    {info.position_decision?.label || info.latest_scan_pa_action || '等待确认'}
                                </div>
                                <p className="mt-1 text-xs font-semibold text-slate-500">
                                    {info.position_decision?.action || (info.trade_bucket === 'TRADE' ? '14:40后确认接近入场线、未破失效线再小仓' : info.trade_bucket === 'BLOCK' ? '只复盘不交易，不追高开或结构失效票' : '先观察，等回踩/放量站稳')}
                                </p>
                            </div>
                        </div>
                        {Array.isArray(info.trade_blockers) && info.trade_blockers.length > 0 && (
                            <div className="flex flex-wrap gap-1.5 lg:max-w-md">
                                {info.trade_blockers.slice(0, 4).map((reason: string, idx: number) => (
                                    <span key={idx} className="text-[10px] px-2 py-0.5 bg-rose-50 text-rose-600 rounded-full font-bold border border-rose-100">{reason}</span>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* ═══ Trading Metrics Strip ═══ */}
            {info.is_paper_trade && (
                <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                    <MetricCard label="买入均价" value={`¥${info.buy_price.toFixed(2)}`} color="text-indigo-600" icon={<DollarSign size={14} />} />
                    <MetricCard label="初始止损" value={`¥${(info.initial_stop_price || 0).toFixed(2)}`} color="text-rose-500" icon={<ShieldAlert size={14} />} />
                    <MetricCard label="执行风控" value={`¥${activeStopPrice.toFixed(2)}`} color="text-rose-600" icon={<ShieldAlert size={14} />} />
                    <MetricCard label="目标止盈" value={`¥${info.take_profit_price.toFixed(2)}`} color="text-emerald-600" icon={<Target size={14} />} />
                    <MetricCard
                        label="当前盈亏"
                        value={`${info.pl_pct >= 0 ? '+' : ''}${info.pl_pct.toFixed(2)}%`}
                        color={info.pl_pct >= 0 ? "text-rose-600" : "text-emerald-600"}
                        icon={info.pl_pct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                    />
                    <MetricCard label="持仓天数" value={`${info.hold_days}天`} color="text-slate-600" icon={<Clock size={14} />} />
                </div>
            )}
            {info.is_paper_trade && (info.entry_source || info.entry_signal_date || info.entry_reason_snapshot) && (
                <div className="glass-card px-4 py-3 text-xs font-semibold text-slate-500 flex flex-wrap gap-x-5 gap-y-1">
                    <span>买入依据：{info.entry_reason_snapshot || '未记录'}</span>
                    <span>买入日期：{info.entry_date || '未记录'}</span>
                    <span>信号日期：{info.entry_signal_date || '未记录'}</span>
                    <span>价格来源：{info.entry_source || '未记录'}</span>
                    <span>当前价来源：{priceSourceLabel}{info.price_updated_at ? ` · ${info.price_updated_at}` : ''}</span>
                </div>
            )}

            {/* ═══ Money Flow Confirmation ═══ */}
            <div className="glass-card p-5 border border-slate-200">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-start gap-3">
                        <div className={cn(
                            "w-10 h-10 rounded-xl flex items-center justify-center",
                            moneyBias === 'inflow' ? "bg-rose-50 text-rose-600" :
                                moneyBias === 'outflow' ? "bg-emerald-50 text-emerald-600" :
                                    "bg-slate-50 text-slate-500"
                        )}>
                            <DollarSign size={20} />
                        </div>
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">资金确认</h4>
                                <span className={cn(
                                    "px-2 py-0.5 rounded-md border text-[10px] font-black",
                                    moneyFlow.status === 'ok' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                        moneyFlow.status === 'stale' ? "bg-amber-50 text-amber-700 border-amber-100" :
                                            "bg-slate-50 text-slate-500 border-slate-100"
                                )}>
                                    {moneyFlow.status === 'ok' ? '已更新' : moneyFlow.status === 'stale' ? '缓存数据' : '暂不可用'}
                                </span>
                                {moneyFlow.cache_hit && <span className="px-2 py-0.5 rounded-md bg-slate-50 border border-slate-100 text-[10px] font-black text-slate-500">缓存</span>}
                            </div>
                            <div className="mt-1 text-base font-black text-slate-800">
                                {moneyBias === 'inflow' ? `${moneyDirectionLabel}偏流入` : moneyBias === 'outflow' ? `${moneyDirectionLabel}偏流出` : '资金方向中性'}
                            </div>
                            <p className="mt-1 text-xs font-semibold text-slate-500">
                                {moneySummary.consecutive_days
                                    ? `${moneySummary.consecutive_days}日连续${moneySummary.consecutive_direction === 'inflow' ? '流入' : moneySummary.consecutive_direction === 'outflow' ? '流出' : '震荡'}`
                                    : '等待资金流确认'}
                                {moneyFlow.updated_at ? ` · ${String(moneyFlow.updated_at).slice(0, 19)}` : ''}
                            </p>
                        </div>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-2 min-w-0 lg:min-w-[620px]">
                        <MoneyFlowItem label={isMainMoneyFlow ? "今日主力" : "当前净额"} value={formatMoneyYi(moneyLatest.main_net_inflow_yi)} hot={Number(moneyLatest.main_net_inflow_yi || 0) > 0} />
                        <MoneyFlowItem label={isMainMoneyFlow ? "5日主力" : "5日累计"} value={isMainMoneyFlow ? formatMoneyYi(moneySummary.main_net_5d_yi) : '--'} hot={isMainMoneyFlow && Number(moneySummary.main_net_5d_yi || 0) > 0} />
                        <MoneyFlowItem label="净占比" value={isMainMoneyFlow && moneyLatest.main_net_ratio != null ? `${Number(moneyLatest.main_net_ratio).toFixed(2)}%` : '--'} hot={isMainMoneyFlow && Number(moneyLatest.main_net_ratio || 0) > 0} />
                        <MoneyFlowItem label="超大单" value={isMainMoneyFlow ? formatMoneyYi(moneyLatest.super_net_inflow_yi) : '--'} hot={isMainMoneyFlow && Number(moneyLatest.super_net_inflow_yi || 0) > 0} />
                        <MoneyFlowItem label="大单" value={isMainMoneyFlow ? formatMoneyYi(moneyLatest.big_net_inflow_yi) : '--'} hot={isMainMoneyFlow && Number(moneyLatest.big_net_inflow_yi || 0) > 0} />
                    </div>
                </div>
            </div>

            <StockResearchCard
                data={research}
                loading={researchLoading}
                onRefresh={() => fetchResearch(true)}
            />

            <ResearchThesisPanel code={code} strategyType={chartStrategy} research={research} />

            {/* ═══ AI Operation Suggestion ═══ */}
            <div className={cn(
                "glass-card p-6 border-2 shadow-xl transition-all",
                actionStyle.bg, actionStyle.border, actionStyle.glow
            )}>
                <div className="flex items-start gap-5">
                    <div className={cn(
                        "w-14 h-14 rounded-2xl flex items-center justify-center shrink-0 shadow-lg",
                        actionStyle.bg, actionStyle.text
                    )}>
                        <ActionIcon size={28} />
                    </div>
                    <div className="flex-1 space-y-3">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <h3 className={cn("text-lg font-black", actionStyle.text)}>
                                    {suggestion.action_label}
                                </h3>
                            </div>
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">信心</span>
                                <div className="w-20 h-2 bg-slate-200 rounded-full overflow-hidden">
                                    <div
                                        className={cn("h-full rounded-full transition-all duration-500",
                                            suggestion.confidence > 0.7 ? "bg-emerald-500" :
                                            suggestion.confidence > 0.4 ? "bg-amber-500" : "bg-rose-500"
                                        )}
                                        style={{ width: `${suggestion.confidence * 100}%` }}
                                    />
                                </div>
                                <span className="text-xs font-black font-mono text-slate-500">{(suggestion.confidence * 100).toFixed(0)}%</span>
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            {suggestion.reasoning.map((reason, i) => (
                                <p key={i} className="text-xs font-semibold text-slate-600 leading-relaxed">
                                    {reason}
                                </p>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {/* ═══ Three-Column Info Grid ═══ */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Column 1: Concept Themes */}
                <div className="glass-card p-6 space-y-4">
                    <div className="flex items-center gap-2">
                        <Layers size={16} className="text-indigo-500" />
                        <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">概念板块</h4>
                    </div>
                    {data.concepts.length > 0 ? (
                        <div className="space-y-2">
                            {data.concepts.map((c, i) => (
                                <div key={i} className="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-100 hover:border-indigo-200 transition-all">
                                    <div className="flex items-center gap-2">
                                        <Tag size={12} className="text-indigo-400" />
                                        <span className="text-sm font-bold text-slate-700">{c.name}</span>
                                    </div>
                                    <span className={cn(
                                        "text-xs font-black px-2 py-0.5 rounded-lg",
                                        c.pct >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"
                                    )}>
                                        {c.pct >= 0 ? '+' : ''}{c.pct}%
                                    </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="text-xs text-slate-400 italic py-4">暂无概念板块数据</p>
                    )}
                </div>

                {/* Column 2: Financial Profile */}
                <div className="glass-card p-6 space-y-4">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <BarChart3 size={16} className="text-amber-500" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">财务画像</h4>
                        </div>
                        {fin.label && fin.label !== '未知' && (
                            <span className="text-[10px] font-black px-2 py-0.5 rounded-lg bg-amber-50 text-amber-600 border border-amber-100">
                                {fin.label}
                            </span>
                        )}
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <FinancialItem label="ROE" value={fin.roe != null ? `${fin.roe}%` : 'N/A'} />
                        <FinancialItem label="PE (TTM)" value={fin.pe_ttm != null ? `${fin.pe_ttm}x` : 'N/A'} sub={fin.pe_percentile} />
                        <FinancialItem
                            label="净利YOY"
                            value={fin.net_profit_yoy != null ? `${fin.net_profit_yoy >= 0 ? '+' : ''}${fin.net_profit_yoy}%` : 'N/A'}
                            highlight={fin.net_profit_yoy != null && fin.net_profit_yoy > 0}
                        />
                        <FinancialItem
                            label="营收YOY"
                            value={fin.revenue_yoy != null ? `${fin.revenue_yoy >= 0 ? '+' : ''}${fin.revenue_yoy}%` : 'N/A'}
                            highlight={fin.revenue_yoy != null && fin.revenue_yoy > 0}
                        />
                    </div>
                    {fin.mkt_cap_yi && (
                        <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-100">
                            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">总市值</span>
                            <span className="text-sm font-black text-slate-700">{fin.mkt_cap_yi}亿</span>
                        </div>
                    )}
                </div>

                {/* Column 3: Risk Assessment */}
                <div className="glass-card p-6 space-y-4">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <ShieldCheck size={16} className="text-rose-500" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">风险评估</h4>
                        </div>
                        <span className={cn(
                            "text-xs font-black px-3 py-1 rounded-xl border shadow-sm",
                            riskColors[risk.risk_level] || riskColors['中']
                        )}>
                            风险 {risk.risk_level}
                        </span>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <RiskItem label="波动性" value={risk.volatility} />
                        <RiskItem label="流动性" value={risk.liquidity} />
                        <RiskItem label="板块风险" value={risk.sector_risk} />
                        <div className="p-3 bg-slate-50 rounded-xl border border-slate-100">
                            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-1">大盘环境</p>
                            <span className={cn(
                                "text-[10px] font-black px-2 py-0.5 rounded-lg",
                                regimeLabels[risk.market_regime]?.color || 'text-slate-500 bg-slate-100'
                            )}>
                                {regimeLabels[risk.market_regime]?.label || '未知'}
                            </span>
                        </div>
                    </div>

                    {risk.warnings.length > 0 && (
                        <div className="space-y-1.5">
                            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest flex items-center gap-1">
                                <AlertTriangle size={10} /> 风险提示
                            </p>
                            {risk.warnings.map((w, i) => (
                                <div key={i} className="flex items-start gap-2 p-2.5 bg-amber-50 border border-amber-100 rounded-xl">
                                    <AlertTriangle size={11} className="text-amber-500 shrink-0 mt-0.5" />
                                    <span className="text-[11px] font-semibold text-amber-700 leading-relaxed">{w}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* ═══ Remark / Discipline ═══ */}
            {info.paper_remark && (
                <div className="glass-card p-5">
                    <div className="flex items-center gap-2 mb-2">
                        <Zap size={14} className="text-indigo-500" />
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">📝 持仓纪律 / 备注</span>
                    </div>
                    <p className="text-sm font-semibold text-slate-600 italic leading-relaxed pl-6">
                        "{info.paper_remark}"
                    </p>
                </div>
            )}
            {toast && (
                <div className={cn(
                    "fixed right-6 bottom-6 z-50 rounded-2xl px-4 py-3 text-sm font-bold shadow-2xl",
                    toast.type === 'success' ? "bg-emerald-600 text-white" : "bg-rose-600 text-white"
                )}>
                    {toast.message}
                </div>
            )}
        </div>
    );
}

// ── Sub-components ──

function StockResearchCard({
    data,
    loading,
    onRefresh,
}: {
    data: StockResearchData | null;
    loading: boolean;
    onRefresh: () => void;
}) {
    const summary = data?.summary || {};
    const quality = data?.evidence_quality;
    const memoText = (items?: Array<{ text?: string } | string>) => (items || [])
        .map(item => typeof item === 'string' ? item : item.text)
        .filter(Boolean) as string[];
    const bullCase = memoText(data?.decision_memo?.bull_case);
    const bearCase = memoText(data?.decision_memo?.bear_case);
    const opportunities = summary.opportunity_flags || [];
    const risks = summary.risk_flags || [];
    const evidence: Array<{ title?: string; meta: string; url?: string; kind: string }> = [
        ...(data?.announcements || []).slice(0, 3).map(item => ({
            title: item.title,
            meta: `${item.date || '--'} · ${item.type || '公告'}`,
            url: item.url,
            kind: '公告',
        })),
        ...(data?.news || []).slice(0, 3).map(item => ({
            title: item.title,
            meta: `${item.time || '--'} · ${item.source || '新闻'}`,
            url: item.url,
            kind: '新闻',
        })),
        ...(data?.reports || []).slice(0, 2).map(item => ({
            title: item.title,
            meta: `${item.publish_date || '--'} · ${item.org || '研报'}${item.rating ? ` · ${item.rating}` : ''}`,
            kind: '研报',
        })),
    ];

    return (
        <div className="glass-card p-5 border border-slate-200">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex items-start gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                        <FileText size={20} />
                    </div>
                    <div>
                        <div className="flex flex-wrap items-center gap-2">
                            <h4 className="text-xs font-black uppercase tracking-widest text-slate-400">事件与基本面研究</h4>
                            {data?.status && (
                                <span className={cn(
                                    "rounded-md border px-2 py-0.5 text-[10px] font-black",
                                    data.status === 'ok' ? "border-emerald-100 bg-emerald-50 text-emerald-700" : "border-amber-100 bg-amber-50 text-amber-700"
                                )}>
                                    {data.status === 'ok' ? '数据完整' : '部分数据可用'}
                                </span>
                            )}
                            {data?.cache_hit && <span className="rounded-md border border-slate-100 bg-slate-50 px-2 py-0.5 text-[10px] font-black text-slate-500">缓存</span>}
                        </div>
                        <p className="mt-1 text-base font-black text-slate-800">{summary.label || (loading ? '正在加载研究证据' : '暂无研究结论')}</p>
                        <p className="mt-1 text-xs font-semibold text-slate-500">
                            仅作证据展示，不参与A级、交易分或买卖资格计算
                            {data?.updated_at ? ` · ${String(data.updated_at).slice(0, 19)}` : ''}
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={onRefresh}
                    disabled={loading}
                    className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-600 hover:text-indigo-600 disabled:opacity-50"
                    title="刷新研究数据"
                >
                    <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                    刷新证据
                </button>
            </div>

            {quality && (
                <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-4">
                    <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-3">
                        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">证据质量</div>
                        <div className={cn(
                            "mt-1 text-lg font-black",
                            ['A', 'B'].includes(quality.grade || '') ? 'text-emerald-600' :
                                quality.grade === 'C' ? 'text-amber-600' :
                                    ['D', 'F'].includes(quality.grade || '') ? 'text-rose-600' : 'text-slate-500'
                        )}>{quality.grade || 'UNRATED'} · {quality.status || 'NOT_ASSESSED'}</div>
                        <p className="mt-1 text-xs font-bold text-slate-500">{quality.summary || '暂无评级说明'}</p>
                    </div>
                    <EvidenceMemoList title="看多依据" items={bullCase} tone="bull" />
                    <EvidenceMemoList title="主要反证" items={bearCase} tone="bear" />
                    <EvidenceMemoList
                        title="失效 / 未知"
                        items={[...(data?.decision_memo?.invalidation_conditions || []), ...(data?.decision_memo?.unknowns || [])]}
                        tone="unknown"
                    />
                </div>
            )}

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
                <ResearchFlagList title="催化证据" items={opportunities} tone="opportunity" />
                <ResearchFlagList title="风险证据" items={risks} tone="risk" />
                <div>
                    <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-400">
                        <Newspaper size={13} /> 最新公告 / 新闻 / 研报
                    </div>
                    <div className="space-y-2">
                        {evidence.slice(0, 6).map((item, idx) => (
                            <div key={`${item.kind}-${item.title}-${idx}`} className="border-b border-slate-100 pb-2 last:border-b-0">
                                <div className="flex items-start gap-2">
                                    <span className="mt-0.5 shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-black text-slate-500">{item.kind}</span>
                                    <div className="min-w-0">
                                        {item.url ? (
                                            <a href={item.url} target="_blank" rel="noreferrer" className="flex items-start gap-1 text-xs font-black text-slate-700 hover:text-indigo-600">
                                                <span>{item.title || '未命名证据'}</span><ExternalLink size={10} className="mt-0.5 shrink-0" />
                                            </a>
                                        ) : <p className="text-xs font-black text-slate-700">{item.title || '未命名证据'}</p>}
                                        <p className="mt-0.5 text-[10px] font-bold text-slate-400">{item.meta}</p>
                                    </div>
                                </div>
                            </div>
                        ))}
                        {!evidence.length && <p className="text-xs font-bold text-slate-400">暂无最新公开证据</p>}
                    </div>
                </div>
            </div>
        </div>
    );
}

function EvidenceMemoList({ title, items, tone }: { title: string; items: string[]; tone: 'bull' | 'bear' | 'unknown' }) {
    const toneClass = tone === 'bull' ? 'border-rose-100 bg-rose-50 text-rose-700'
        : tone === 'bear' ? 'border-amber-100 bg-amber-50 text-amber-800'
            : 'border-slate-100 bg-slate-50 text-slate-600';
    return (
        <div className={cn("rounded-md border px-3 py-3", toneClass)}>
            <div className="text-[10px] font-black uppercase tracking-widest opacity-70">{title}</div>
            <div className="mt-2 space-y-1">
                {items.slice(0, 3).map((item, index) => <p key={`${title}-${index}`} className="text-xs font-bold leading-relaxed">{item}</p>)}
                {!items.length && <p className="text-xs font-bold opacity-60">暂无</p>}
            </div>
        </div>
    );
}

function ResearchFlagList({ title, items, tone }: { title: string; items: string[]; tone: 'opportunity' | 'risk' }) {
    const opportunity = tone === 'opportunity';
    return (
        <div>
            <div className={cn("mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest", opportunity ? "text-rose-500" : "text-amber-600")}>
                {opportunity ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}{title}
            </div>
            <div className="space-y-2">
                {items.slice(0, 6).map((item, idx) => (
                    <div key={`${item}-${idx}`} className={cn(
                        "rounded-md border px-3 py-2 text-xs font-bold leading-relaxed",
                        opportunity ? "border-rose-100 bg-rose-50 text-rose-700" : "border-amber-100 bg-amber-50 text-amber-800"
                    )}>{item}</div>
                ))}
                {!items.length && <p className="text-xs font-bold text-slate-400">暂无明确{title}</p>}
            </div>
        </div>
    );
}

function ResearchThesisPanel({ code, strategyType, research }: { code: string; strategyType: string; research: StockResearchData | null }) {
    const [items, setItems] = useState<any[]>([]);
    const [open, setOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState({ thesis: '', confirmation: '', invalidation: '' });

    const load = useCallback(async () => {
        try {
            const res = await api.get(`/api/stock/${code}/research-theses`);
            setItems(res.data?.items || []);
        } catch (err) {
            console.warn('Research theses fetch failed:', err);
        }
    }, [code]);

    useEffect(() => { load(); }, [load]);

    const save = async () => {
        if (!draft.thesis.trim()) return;
        setSaving(true);
        try {
            await api.post(`/api/stock/${code}/research-theses`, {
                title: `研究论点 ${new Date().toISOString().slice(0, 10)}`,
                thesis_text: draft.thesis,
                confirmation_condition: draft.confirmation,
                invalidation_condition: draft.invalidation,
                catalysts: research?.summary?.opportunity_flags || [],
                risks: research?.summary?.risk_flags || [],
                strategy_type: strategyType,
            });
            setDraft({ thesis: '', confirmation: '', invalidation: '' });
            setOpen(false);
            await load();
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="glass-card p-5 border border-slate-200">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="flex items-center gap-2"><BookOpen size={16} className="text-indigo-600" /><h4 className="text-xs font-black uppercase tracking-widest text-slate-400">研究论点快照</h4></div>
                    <p className="mt-1 text-xs font-semibold text-slate-500">保存当时的判断、确认条件、失效条件和当前研究证据</p>
                </div>
                <button type="button" onClick={() => setOpen(value => !value)} className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-600 hover:text-indigo-600">
                    {open ? '取消' : '新建论点'}
                </button>
            </div>

            {open && (
                <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
                    <textarea value={draft.thesis} onChange={event => setDraft({ ...draft, thesis: event.target.value })} placeholder="核心研究判断" className="min-h-24 rounded-md border border-slate-200 bg-white p-3 text-sm font-semibold text-slate-700 outline-none focus:border-indigo-400 lg:col-span-3" />
                    <input value={draft.confirmation} onChange={event => setDraft({ ...draft, confirmation: event.target.value })} placeholder="确认条件，例如放量站回确认价" className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold outline-none focus:border-indigo-400" />
                    <input value={draft.invalidation} onChange={event => setDraft({ ...draft, invalidation: event.target.value })} placeholder="失效条件，例如跌破结构止损" className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold outline-none focus:border-indigo-400" />
                    <button type="button" onClick={save} disabled={saving || !draft.thesis.trim()} className="inline-flex items-center justify-center gap-2 rounded-md bg-indigo-600 px-3 py-2 text-sm font-black text-white disabled:opacity-50">
                        <Save size={14} />{saving ? '保存中' : '保存快照'}
                    </button>
                </div>
            )}

            <div className="mt-4 space-y-2">
                {items.slice(0, 5).map(item => (
                    <div key={item.id} className="grid grid-cols-1 gap-2 border-t border-slate-100 pt-3 first:border-t-0 first:pt-0 lg:grid-cols-[140px_1fr_1fr]">
                        <div><p className="text-xs font-black text-slate-700">{item.thesis_date}</p><p className="text-[10px] font-bold text-slate-400">{item.strategy_type || '未关联策略'} · {item.status}</p></div>
                        <div><p className="text-sm font-black text-slate-800">{item.thesis_text}</p><p className="mt-1 text-[10px] font-bold text-slate-400">确认：{item.confirmation_condition || '未记录'}</p></div>
                        <p className="text-xs font-bold text-rose-600">失效：{item.invalidation_condition || '未记录'}</p>
                    </div>
                ))}
                {!items.length && !open && <p className="py-3 text-center text-xs font-bold text-slate-400">尚未保存研究论点</p>}
            </div>
        </div>
    );
}

function MetricCard({ label, value, color, icon }: { label: string; value: string; color: string; icon: React.ReactNode }) {
    return (
        <div className="glass-card p-4 flex items-center gap-3">
            <div className={cn("p-2 rounded-xl bg-slate-50", color)}>{icon}</div>
            <div>
                <p className="text-[9px] font-black text-slate-400 uppercase tracking-wider">{label}</p>
                <p className={cn("text-lg font-black font-mono", color)}>{value}</p>
            </div>
        </div>
    );
}

function formatMoneyYi(value?: number | null) {
    if (value === undefined || value === null || Number.isNaN(Number(value))) return '--';
    const num = Number(value);
    return `${num >= 0 ? '+' : ''}${num.toFixed(2)}亿`;
}

function MoneyFlowItem({ label, value, hot = false }: { label: string; value: string; hot?: boolean }) {
    return (
        <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
            <p className="text-[9px] font-black text-slate-400">{label}</p>
            <p className={cn("mt-1 text-sm font-black", hot ? "text-rose-600" : value.startsWith('-') ? "text-emerald-600" : "text-slate-700")}>{value}</p>
        </div>
    );
}

function FinancialItem({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: boolean }) {
    return (
        <div className="p-3 bg-slate-50 rounded-xl border border-slate-100">
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-0.5">{label}</p>
            <p className={cn("text-sm font-black", highlight ? "text-rose-600" : "text-slate-700")}>{value}</p>
            {sub && <p className="text-[9px] font-bold text-slate-400 mt-0.5">{sub}</p>}
        </div>
    );
}

function RiskItem({ label, value }: { label: string; value: string }) {
    const valueColors: Record<string, string> = {
        '低': 'text-emerald-600', '良好': 'text-emerald-600', '充裕': 'text-emerald-600',
        '中': 'text-amber-600', '中等': 'text-amber-600',
        '高': 'text-rose-600', '偏弱': 'text-rose-600',
    };
    return (
        <div className="p-3 bg-slate-50 rounded-xl border border-slate-100">
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
            <p className={cn("text-sm font-black", valueColors[value] || 'text-slate-600')}>{value}</p>
        </div>
    );
}
