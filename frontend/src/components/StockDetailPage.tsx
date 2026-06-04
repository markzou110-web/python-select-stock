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
    Star
} from 'lucide-react';
import { cn } from '@/lib/utils';
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
}

export default function StockDetailPage({ code, name, onBack }: StockDetailPageProps) {
    const [data, setData] = useState<FullAnalysisData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [addingAction, setAddingAction] = useState<'paper' | 'watchlist' | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

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

    useEffect(() => {
        fetchData(true);
        const timer = window.setInterval(() => fetchData(false), 30000);
        return () => window.clearInterval(timer);
    }, [fetchData]);

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
    const activeStopPrice = info.active_stop_price || info.stop_price || 0;
    const chartStrategy = data.signals?.strategy_type || info.chart_strategy_type || info.strategy_type || 'squeeze';
    const strategyLabels: Record<string, string> = {
        squeeze: '均线粘合',
        pine: 'Pine 多指标',
        tv_zp: 'TV ZP',
        consensus: 'Azul 共识',
        both: '双重共振',
    };
    const buySignalCount = data.signals?.buy_count ?? data.signals?.buy_signals?.length ?? 0;
    const sellSignalCount = data.signals?.sell_count ?? data.signals?.sell_signals?.length ?? 0;
    const priceSourceLabel = info.price_source === 'realtime_snapshot'
        ? '实时快照'
        : info.price_source === 'paper_cached_price'
            ? '持仓缓存价'
            : '日线收盘价';

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
            if (res.data?.status !== 'success') {
                showToast(res.data?.detail || '加入模拟盘失败', 'error');
                return;
            }
            showToast(`${info.名称} 已加入模拟盘`);
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
                            <span className={cn(
                                "text-sm font-black px-2 py-0.5 rounded-lg",
                                info['涨幅%'] >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"
                            )}>
                                {info['涨幅%'] >= 0 ? '+' : ''}{info['涨幅%']}%
                            </span>
                            <span className="text-xs font-bold text-slate-400">综合强度 {info.Score}</span>
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
                                    data.price_action.pa_trade_plan.action === 'READY' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                        data.price_action.pa_trade_plan.action === 'AVOID' ? "bg-rose-50 text-rose-700 border-rose-100" :
                                            "bg-amber-50 text-amber-700 border-amber-100"
                                )}>
                                    {data.price_action.pa_trade_plan.action_label}
                                </span>
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
                                    {info.final_trade_score != null && (
                                        <span className="px-2 py-0.5 rounded-md bg-slate-50 border border-slate-100 text-[10px] font-black text-slate-500">
                                            交易分 {Number(info.final_trade_score).toFixed(0)}
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
