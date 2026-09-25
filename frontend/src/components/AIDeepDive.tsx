"use client";

import React, { useEffect, useState } from 'react';
import {
    X,
    Zap,
    ShieldCheck,
    Activity,
    Info,
    ChevronRight,
    BarChart3,
    Loader2,
    Plus,
    AlertTriangle,
    Bot
} from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';
import { useTradeStore } from '@/stores/tradeStore';
import SplitKLineCharts, { ChipDistribution } from './SplitKLineCharts';

interface AIDeepDiveProps {
    stock: any;
    onClose: () => void;
}

function toFiniteNumber(...values: unknown[]): number | null {
    for (const value of values) {
        if (value === null || value === undefined || value === '') continue;
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
    }
    return null;
}

function toPercentNumber(...values: unknown[]): number | null {
    for (const value of values) {
        if (typeof value === 'string' && value.trim().endsWith('%')) {
            const parsed = Number(value.trim().slice(0, -1));
            if (Number.isFinite(parsed)) return parsed;
        }
        const parsed = toFiniteNumber(value);
        if (parsed !== null) return parsed;
    }
    return null;
}

function formatNumber(value: number | null, digits = 1): string {
    return value === null ? '暂无数据' : value.toFixed(digits);
}

function formatPercent(value: number | null, digits = 1): string {
    return value === null ? '暂无数据' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

function formatRate(value: number | null, digits = 1): string {
    return value === null ? '暂无数据' : `${value.toFixed(digits)}%`;
}

export default function AIDeepDive({ stock, onClose }: AIDeepDiveProps) {
    const [loading, setLoading] = useState(true);
    const [chartData, setChartData] = useState<any[]>([]);
    const [stockInfo, setStockInfo] = useState<any>(null);
    const [priceAction, setPriceAction] = useState<any>(null);
    const [priceActionLines, setPriceActionLines] = useState<any[]>([]);
    const [chipDistribution, setChipDistribution] = useState<ChipDistribution | null>(null);
    const [loadedCode, setLoadedCode] = useState<string | null>(null);
    const [aiVerdict, setAiVerdict] = useState<any>(null);
    const [aiLoading, setAiLoading] = useState(false);

    // Simulated trading addition states
    const [showRemarkModal, setShowRemarkModal] = useState(false);
    const [remarkText, setRemarkText] = useState('');
    const [isAdding, setIsAdding] = useState(false);
    const [addTradeMode, setAddTradeMode] = useState<'SIMULATED' | 'REAL'>('SIMULATED');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 2500);
    };

    useEffect(() => {
        let cancelled = false;

        const fetchData = async () => {
            setLoading(true);
            setLoadedCode(null);
            setChartData([]);
            setStockInfo(null);
            setPriceAction(null);
            setPriceActionLines([]);
            setChipDistribution(null);
            setAiVerdict(null);
            try {
                const res = await api.get(`/api/stock/detail?code=${stock.代码}`);
                if (cancelled) return;
                const data = res.data.data;
                setChartData(data);
                setPriceAction(res.data.price_action || null);
                setPriceActionLines(res.data.price_action_lines || []);
                setChipDistribution(res.data.chip_distribution || null);

                // Detail data supplies current measurements. Strategy scan fields stay tied to
                // the selected scan row so a generic detail fallback cannot masquerade as a signal.
                const info = res.data.stock_info || {};
                const selectedScanScore = stock.display_signal_score ?? stock.latest_scan_score ?? stock.Score ?? null;
                const selectedScanDate = stock.data_date ?? stock.scan_date ?? stock.date ?? stock.日期 ?? null;
                const selectedScanStrategy = stock.strategy_type ?? null;
                const hasSelectedScan = selectedScanScore !== null || selectedScanDate !== null || selectedScanStrategy !== null;
                setStockInfo({
                    ...stock,
                    ...info,
                    Score: hasSelectedScan ? selectedScanScore : info.latest_scan_score ?? null,
                    display_signal_score: hasSelectedScan ? selectedScanScore : info.latest_scan_score ?? null,
                    回测统计: stock.回测统计 ?? null,
                    sector_alignment_score: info.sector_alignment_score ?? stock.sector_alignment_score ?? null,
                    latest_scan_date: hasSelectedScan ? selectedScanDate : info.latest_scan_date ?? null,
                    latest_scan_strategy: hasSelectedScan ? selectedScanStrategy : info.latest_scan_strategy ?? null,
                });
                setLoadedCode(stock.代码);
            } catch (err) {
                if (!cancelled) {
                    console.error("Deep Dive Fetch Error:", err);
                    setLoadedCode(stock.代码);
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        fetchData();
        return () => {
            cancelled = true;
        };
    }, [stock]);

    const hasCurrentDetail = loadedCode === stock.代码;
    const facts = hasCurrentDetail && stockInfo ? stockInfo : stock;
    const currentPriceAction = hasCurrentDetail ? priceAction : null;
    const signalScore = toFiniteNumber(facts.display_signal_score, facts.Score);
    const rsi = toFiniteNumber(facts.RSI);
    const priceActionScore = toFiniteNumber(currentPriceAction?.price_action_score, stock.price_action_score);
    const volumeRatio = toFiniteNumber(currentPriceAction?.pa_volume_ratio, stock.pa_volume_ratio);
    const volumePercentile = toFiniteNumber(currentPriceAction?.pa_volume_ratio_percentile, stock.pa_volume_ratio_percentile);
    const sectorAlignment = toFiniteNumber(facts.sector_alignment_score);
    const moneyFlow5d = toFiniteNumber(facts.money_flow_5d_yi);
    const backtest = stock.回测统计 || null;
    const backtestSamples = toFiniteNumber(backtest?.signal_count, stock.信号次数);
    const rawWinRate = toPercentNumber(backtest?.win_rate, stock.历史胜率);
    const hasBacktest = backtestSamples !== null && backtestSamples > 0;

    const verifiedMetrics = [
        {
            label: '扫描信号分',
            value: signalScore === null ? '暂无记录' : `${signalScore.toFixed(1)}/100`,
            note: facts.latest_scan_date ? `扫描日 ${facts.latest_scan_date}` : '扫描日期未提供',
        },
        {
            label: '价格结构',
            value: formatNumber(priceActionScore, 0),
            note: currentPriceAction?.price_action_regime || currentPriceAction?.price_action_signal || '暂无结构描述',
        },
        {
            label: '当前 RSI',
            value: formatNumber(rsi, 1),
            note: '来自最新日线指标',
        },
        {
            label: '成交量比',
            value: volumeRatio === null ? '暂无数据' : `${volumeRatio.toFixed(2)}x`,
            note: volumePercentile === null ? '暂无量能分位' : `历史分位 ${volumePercentile.toFixed(0)}%`,
        },
        {
            label: '板块联动',
            value: formatNumber(sectorAlignment, 0),
            note: facts.sector_phase || '暂无板块联动数据',
        },
        {
            label: '主力 5 日净额',
            value: moneyFlow5d === null ? '暂无数据' : `${moneyFlow5d >= 0 ? '+' : ''}${moneyFlow5d.toFixed(2)} 亿`,
            note: facts.money_flow_bias || '暂无资金方向数据',
        },
    ];

    const factLines = [
        facts.latest_scan_date
            ? `最近扫描：${facts.latest_scan_date}${facts.latest_scan_strategy ? ` · ${facts.latest_scan_strategy}` : ''}。`
            : signalScore === null
                ? '最近扫描：暂无可核验记录，因此不生成买入结论。'
                : `最近扫描：记录了 ${signalScore.toFixed(1)} 分，但未提供扫描日期，因此不生成买入结论。`,
        currentPriceAction?.price_action_regime || currentPriceAction?.price_action_signal
            ? `价格行为：${[currentPriceAction.price_action_regime, currentPriceAction.price_action_signal].filter(Boolean).join(' · ')}${priceActionScore === null ? '' : `（${priceActionScore.toFixed(0)}分）`}。`
            : '价格行为：暂无结构识别数据。',
        volumeRatio === null
            ? '量能：暂无可核验数据。'
            : `量能：成交量比 ${volumeRatio.toFixed(2)}x${volumePercentile === null ? '' : `，历史分位 ${volumePercentile.toFixed(0)}%`}。`,
        moneyFlow5d === null
            ? '资金：暂无主力资金净额数据，不推断资金加仓。'
            : `资金：主力 5 日净额 ${moneyFlow5d >= 0 ? '+' : ''}${moneyFlow5d.toFixed(2)} 亿元${facts.money_flow_bias ? `，方向为${facts.money_flow_bias}` : ''}。`,
    ];

    const runStockAIAnalysis = async () => {
        setAiLoading(true);
        try {
            const res = await api.post('/api/ai/analyze-stock', {
                code: stock.代码,
                date: facts.latest_scan_date || stock.data_date || stock.scan_date || stock.date || stock.日期,
            });
            if (res.data?.status === 'success' && res.data.analysis) {
                setAiVerdict(res.data.analysis);
                showToast(res.data.message || 'AI研判完成');
            } else {
                showToast(res.data?.message || 'AI研判暂不可用', 'error');
            }
        } catch (err: unknown) {
            const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            showToast(detail || 'AI研判请求失败，原策略结果不受影响', 'error');
        } finally {
            setAiLoading(false);
        }
    };

    const handleAddToWatchlist = async (force: boolean = false, customRemark?: string, mode?: 'SIMULATED' | 'REAL') => {
        const selectedMode = mode || addTradeMode;
        setIsAdding(true);
        try {
            // Determine active values from stock prop or calculated metadata
            const price = stock.现价 || (chartData.length > 0 ? chartData[chartData.length - 1].close : 0.0);
            const res = await api.post('/api/paper/add', {
                code: stock.代码,
                name: stock.名称,
                price: price,
                strategy_type: stock.strategy_type || 'squeeze',
                remark: customRemark || remarkText || '自动扫描并加入',
                force: force,
                trade_mode: selectedMode,
                entry_source: stock.现价 ? 'scan_current_price' : 'last_kline_close',
                entry_signal_date: chartData.length > 0 ? chartData[chartData.length - 1].time : undefined,
                signal_sources: stock.signal_sources,
                entry_reason_snapshot: `${stock.结构 || stock.price_action_pattern || '策略信号'} / Score ${stock.Score ?? '--'}`
            });

            if (res.data.status === 'success' || res.data.status === 'upgraded') {
                const modeLabel = selectedMode === 'REAL' ? '实盘' : '模拟仓';
                showToast(
                    res.data.status === 'upgraded'
                        ? `${stock.名称} 已升级为 ${res.data.signal_sources?.join('+')?.toUpperCase()} 信号持仓，未重复加仓`
                        : `${stock.名称} 已成功加入${modeLabel}！`,
                );
                setShowRemarkModal(false);
                setRemarkText('');
                // Automatically refresh simulated portfolio state
                useTradeStore.getState().fetchTrades();
            } else if (res.data.status === 'warning') {
                // Trigger bypass option
                if (window.confirm(res.data.detail)) {
                    await handleAddToWatchlist(true, customRemark, selectedMode);
                }
            } else {
                showToast(res.data.detail || '加入失败，请重试', 'error');
            }
        } catch (err: any) {
            console.error("Add to Paper Trade Error:", err);
            const errorMsg = err.response?.data?.detail || '网络连接失败，请重试';
            showToast(errorMsg, 'error');
        } finally {
            setIsAdding(false);
        }
    };

    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl h-full flex flex-col overflow-hidden relative">
            {/* Header */}
            <div className="p-6 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 premium-gradient rounded-xl flex items-center justify-center text-white shadow-lg shadow-indigo-200">
                        <Zap size={20} />
                    </div>
                    <div>
                        <h3 className="font-bold text-slate-900">{stock.名称}</h3>
                        <p className="text-[10px] text-slate-400 font-bold tracking-widest uppercase">{stock.代码}</p>
                    </div>
                </div>
                <button
                    onClick={onClose}
                    className="p-2 hover:bg-slate-100 rounded-xl transition-all text-slate-400 hover:text-slate-600"
                >
                    <X size={20} />
                </button>
            </div>

            {/* Scrollable Content */}
            <div className="flex-1 overflow-y-auto p-6 space-y-8 scrollbar-none">
                {/* AI 单股深度研判 */}
                <div className="rounded-2xl border border-indigo-100 bg-gradient-to-br from-indigo-50/70 to-white p-4 space-y-3 shadow-sm">
                    <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                            <Bot size={16} className="text-indigo-500" />
                            <span className="text-xs font-black text-slate-700">AI 个股研判</span>
                            <span className="text-[9px] font-bold text-slate-400">基于策略快照+研究数据，不构成投资建议</span>
                        </div>
                        <button
                            onClick={runStockAIAnalysis}
                            disabled={aiLoading}
                            className="flex items-center gap-1.5 rounded-xl bg-indigo-500 px-3 py-1.5 text-xs font-black text-white shadow-md shadow-indigo-200 transition-all hover:bg-indigo-600 disabled:opacity-60"
                        >
                            {aiLoading ? <Loader2 size={14} className="animate-spin" /> : <Bot size={14} />}
                            {aiLoading ? '研判中…' : aiVerdict ? '重新研判' : '生成研判'}
                        </button>
                    </div>
                    {aiVerdict && (
                        <div className="space-y-3">
                            <div className="flex flex-wrap items-center gap-2">
                                <span className={cn(
                                    "rounded-lg px-2.5 py-1 text-xs font-black shadow-sm border",
                                    aiVerdict.action === 'BUY' ? 'bg-emerald-50 border-emerald-100 text-emerald-600'
                                    : aiVerdict.action === 'AVOID' ? 'bg-rose-50 border-rose-100 text-rose-600'
                                    : 'bg-amber-50 border-amber-100 text-amber-600'
                                )}>
                                    {aiVerdict.action}
                                </span>
                                <span className="text-xs font-black text-slate-500">置信度 {aiVerdict.confidence ?? 0}/100</span>
                                {aiVerdict.trend_view && (
                                    <span className="rounded-lg bg-white px-2 py-1 text-[10px] font-black text-slate-500 border border-slate-100">{aiVerdict.trend_view}</span>
                                )}
                                {aiVerdict.guardrail_adjusted && (
                                    <span className="rounded-lg bg-orange-50 px-2 py-1 text-[10px] font-black text-orange-500 border border-orange-100">风控护栏已降级</span>
                                )}
                            </div>
                            <p className="text-xs font-bold leading-relaxed text-slate-700">{aiVerdict.summary}</p>
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                {aiVerdict.positive_factors?.length > 0 && (
                                    <div className="rounded-xl bg-white/80 border border-emerald-50 p-2.5">
                                        <p className="mb-1 text-[9px] font-black uppercase tracking-widest text-emerald-500">支持因素</p>
                                        <ul className="space-y-1">
                                            {aiVerdict.positive_factors.map((item: string, idx: number) => (
                                                <li key={idx} className="text-[11px] font-bold text-slate-600">+ {item}</li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                                {aiVerdict.risk_factors?.length > 0 && (
                                    <div className="rounded-xl bg-white/80 border border-rose-50 p-2.5">
                                        <p className="mb-1 text-[9px] font-black uppercase tracking-widest text-rose-500">风险因素</p>
                                        <ul className="space-y-1">
                                            {aiVerdict.risk_factors.map((item: string, idx: number) => (
                                                <li key={idx} className="text-[11px] font-bold text-slate-600">- {item}</li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </div>
                            {aiVerdict.catalysts?.length > 0 && (
                                <div className="rounded-xl bg-white/80 border border-indigo-50 p-2.5">
                                    <p className="mb-1 text-[9px] font-black uppercase tracking-widest text-indigo-400">近期事件（研究快照）</p>
                                    <ul className="space-y-1">
                                        {aiVerdict.catalysts.map((item: string, idx: number) => (
                                            <li key={idx} className="text-[11px] font-bold text-slate-600">· {item}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {aiVerdict.key_levels && (
                                <div className="rounded-xl bg-white/80 border border-slate-100 p-2.5">
                                    <p className="mb-1 text-[9px] font-black uppercase tracking-widest text-slate-400">关键价位应对</p>
                                    <p className="text-[11px] font-bold leading-relaxed text-slate-600">{aiVerdict.key_levels}</p>
                                </div>
                            )}
                            {aiVerdict.data_limitations?.length > 0 && (
                                <p className="text-[10px] font-bold leading-relaxed text-slate-400">
                                    数据局限：{aiVerdict.data_limitations.join('；')}
                                </p>
                            )}
                        </div>
                    )}
                </div>

                {/* Simulated Trading Position Dashboard */}
                {stockInfo?.is_paper_trade && (
                    <div className="glass-card p-5 bg-gradient-to-br from-indigo-50/70 to-purple-50/30 border border-indigo-100 rounded-3xl space-y-4 shadow-lg shadow-indigo-50/50 animate-in fade-in zoom-in-95 duration-300">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <div className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                                <span className="text-[10px] font-black text-indigo-600 uppercase tracking-widest bg-indigo-50 px-2 py-0.5 rounded-md border border-indigo-100">拟合实盘持仓中</span>
                            </div>
                            <span className={cn(
                                "text-xs font-black px-2.5 py-1 rounded-xl shadow-sm border",
                                (stockInfo.现价 - stockInfo.buy_price) >= 0 
                                    ? "bg-rose-50 border-rose-100 text-rose-600 shadow-rose-50/50" 
                                    : "bg-emerald-50 border-emerald-100 text-emerald-600 shadow-emerald-50/50"
                            )}>
                                {(stockInfo.现价 - stockInfo.buy_price) >= 0 ? '+' : ''}
                                {(((stockInfo.现价 - stockInfo.buy_price) / stockInfo.buy_price) * 100).toFixed(2)}%
                            </span>
                        </div>

                        <div className="grid grid-cols-3 gap-3">
                            <div className="p-3 bg-white/80 backdrop-blur-md rounded-2xl border border-slate-100 text-center shadow-sm">
                                <p className="text-[9px] font-black text-slate-400 uppercase tracking-wider mb-0.5">买入均价</p>
                                <p className="text-sm font-extrabold text-slate-700 font-mono">¥{stockInfo.buy_price.toFixed(2)}</p>
                            </div>
                            <div className="p-3 bg-white/80 backdrop-blur-md rounded-2xl border border-slate-100 text-center shadow-sm relative group">
                                <p className="text-[9px] font-black text-rose-500 tracking-wider mb-0.5">执行风控</p>
                                <p className="text-sm font-extrabold text-rose-600 font-mono">¥{(stockInfo.active_stop_price || stockInfo.stop_price).toFixed(2)}</p>
                                <span className="text-[8px] font-bold text-rose-400/80 block mt-0.5">
                                    {stockInfo.risk_stage || '分阶段'} · {(100 * (stockInfo.现价 - (stockInfo.active_stop_price || stockInfo.stop_price)) / stockInfo.现价).toFixed(1)}% 缓冲
                                </span>
                            </div>
                            <div className="p-3 bg-white/80 backdrop-blur-md rounded-2xl border border-slate-100 text-center shadow-sm">
                                <p className="text-[9px] font-black text-emerald-500 uppercase tracking-wider mb-0.5">目标止盈</p>
                                <p className="text-sm font-extrabold text-emerald-600 font-mono">¥{stockInfo.take_profit_price.toFixed(2)}</p>
                                <span className="text-[8px] font-bold text-emerald-400/80 block mt-0.5">
                                    距目标 {(100 * (stockInfo.take_profit_price - stockInfo.现价) / stockInfo.现价).toFixed(1)}%
                                </span>
                            </div>
                        </div>

                        {stockInfo.paper_remark && (
                            <div className="p-3 bg-white/50 backdrop-blur-sm rounded-2xl border border-slate-100/80">
                                <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">📝 持仓纪律/备注</p>
                                <p className="text-xs font-semibold text-slate-600 leading-relaxed italic">
                                    “ {stockInfo.paper_remark} ”
                                </p>
                            </div>
                        )}
                        {(stockInfo.entry_source || stockInfo.entry_signal_date || stockInfo.entry_reason_snapshot) && (
                            <div className="p-3 bg-white/50 backdrop-blur-sm rounded-2xl border border-slate-100/80">
                                <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">买入依据</p>
                                <p className="text-xs font-semibold text-slate-600 leading-relaxed">
                                    {stockInfo.entry_reason_snapshot || '未记录'} · 买入 {stockInfo.entry_date || '未记录'} · 信号 {stockInfo.entry_signal_date || '未记录'} · {stockInfo.entry_source || '未记录'}
                                </p>
                            </div>
                        )}
                    </div>
                )}

                {/* 1. Verified data snapshot */}
                <div className="space-y-4">
                    <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                            <Activity size={16} className="text-indigo-500" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">可核验数据快照</h4>
                        </div>
                        <span className="text-[9px] font-bold text-slate-400">缺失项不估算</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        {verifiedMetrics.map(metric => (
                            <div key={metric.label} className="min-w-0 p-4 bg-white border border-slate-100 rounded-2xl shadow-sm">
                                <p className="text-[9px] font-black text-slate-400 uppercase tracking-wider">{metric.label}</p>
                                <p className="mt-1.5 text-lg font-black text-slate-900 break-words">{metric.value}</p>
                                <p className="mt-1 text-[9px] font-semibold text-slate-400 leading-relaxed break-words">{metric.note}</p>
                            </div>
                        ))}
                    </div>
                </div>

                {/* 2. Factual summary */}
                <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <ShieldCheck size={16} className="text-emerald-500" />
                        <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">事实数据简报</h4>
                    </div>
                    <div className="bg-slate-900 rounded-2xl p-5 text-white shadow-lg shadow-slate-200">
                        <ul className="space-y-2.5">
                            {factLines.map(line => (
                                <li key={line} className="flex gap-2 text-xs font-medium leading-relaxed text-slate-200">
                                    <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
                                    <span>{line}</span>
                                </li>
                            ))}
                        </ul>
                        <p className="mt-4 pt-3 border-t border-white/10 text-[9px] font-bold text-slate-400 leading-relaxed">
                            只陈述接口返回的原始数据；不自动推断“高胜率”“板块强势”或“资金持续加仓”。
                        </p>
                    </div>
                </div>

                {/* 3. Historical sample replay */}
                <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                            <BarChart3 size={16} className="text-amber-500" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">历史样本回放</h4>
                        </div>
                        <span className="text-[9px] font-bold text-amber-600 bg-amber-50 px-2 py-1 rounded-lg">非实盘业绩</span>
                    </div>
                    {hasBacktest ? (
                        <>
                        <div className="grid grid-cols-3 gap-3">
                            <div className="p-3 bg-white border border-slate-100 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400 uppercase">样本胜率</p>
                                <p className="text-lg font-black text-slate-900">
                                    {formatRate(rawWinRate, 1)}
                                </p>
                            </div>
                            <div className="p-3 bg-white border border-slate-100 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400 uppercase">保守胜率</p>
                                <p className="text-lg font-black text-slate-900">
                                    {formatRate(toFiniteNumber(backtest?.adjusted_win_rate_99, backtest?.adjusted_win_rate), 1)}
                                </p>
                            </div>
                            <div className="p-3 bg-white border border-slate-100 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400 uppercase">历史样本</p>
                                <p className="text-lg font-black text-indigo-600">{Math.trunc(backtestSamples!)} 笔</p>
                            </div>
                        </div>
                        <div className="grid grid-cols-3 gap-3">
                            <div className="p-3 bg-slate-50 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400">平均收益</p>
                                <p className="text-sm font-black text-slate-700">{formatPercent(toFiniteNumber(backtest?.avg_return), 2)}</p>
                            </div>
                            <div className="p-3 bg-slate-50 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400">最大回撤</p>
                                <p className="text-sm font-black text-slate-700">{formatPercent(toFiniteNumber(backtest?.max_drawdown), 2)}</p>
                            </div>
                            <div className="p-3 bg-slate-50 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400">盈亏比</p>
                                <p className="text-sm font-black text-slate-700">{formatNumber(toFiniteNumber(backtest?.profit_factor), 2)}</p>
                            </div>
                        </div>
                        <p className="px-1 text-[9px] font-semibold text-slate-400 leading-relaxed">
                            平均持仓 {formatNumber(toFiniteNumber(backtest?.avg_hold_days), 1)} 天
                            {toFiniteNumber(backtest?.stop_loss_hits) !== null ? ` · 触发止损 ${Math.trunc(toFiniteNumber(backtest?.stop_loss_hits)!)} 次` : ''}
                            {backtest?.sample_warning ? ` · ${backtest.sample_warning}` : ' · 历史回放不代表未来收益'}
                        </p>
                        </>
                    ) : (
                        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-5 text-center">
                            <p className="text-xs font-bold text-slate-500">当前扫描策略暂无可核验的回测样本</p>
                            <p className="mt-1 text-[9px] font-semibold text-slate-400">不再用其他策略的胜率代替，也不把 0 当作缺省值展示。</p>
                        </div>
                    )}
                </div>

                {/* 4. Split Mini Charts */}
                <div className="space-y-3">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Info size={16} className="text-slate-400" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">60日趋势 / 价格行为</h4>
                        </div>
                    </div>
                    <div className={cn(
                        "bg-slate-50/50 rounded-2xl border border-slate-100 relative group overflow-hidden",
                        (loading || !hasCurrentDetail) && "min-h-[220px] flex items-center justify-center"
                    )}>
                        {loading || !hasCurrentDetail ? (
                            <Loader2 className="animate-spin text-slate-300" size={24} />
                        ) : (
                            <SplitKLineCharts
                                candles={chartData}
                                emaLines={[
                                    { key: 'EMA5', label: 'EMA5', color: '#6366f1' },
                                    { key: 'EMA20', label: 'EMA20', color: '#f59e0b' },
                                ]}
                                priceAction={currentPriceAction}
                                priceActionLines={hasCurrentDetail ? priceActionLines : []}
                                chipDistribution={hasCurrentDetail ? chipDistribution : null}
                                riskLevels={stockInfo}
                                paperLines={stockInfo?.is_paper_trade ? [
                                    { price: stockInfo.buy_price, label: '买入价', color: '#6366f1', date: stockInfo.entry_date },
                                    { price: stockInfo.active_stop_price || stockInfo.stop_price, label: '实时持仓风控线', color: '#f43f5e' },
                                    { price: stockInfo.take_profit_price, label: '止盈价', color: '#10b981' },
                                ] : []}
                                height={stockInfo?.is_paper_trade ? 220 : 190}
                                compact
                            />
                        )}
                    </div>
                </div>
            </div>

            {/* Actions */}
            <div className="p-6 bg-slate-50 border-t border-slate-100">
                {stockInfo?.is_paper_trade ? (
                    <div className="w-full py-3.5 bg-indigo-50 border border-indigo-200 text-indigo-600 rounded-2xl font-black text-sm flex items-center justify-center gap-2 shadow-inner">
                        <ShieldCheck size={18} className="text-indigo-500 animate-pulse shrink-0" />
                        拟合实盘风控实时监控中
                    </div>
                ) : (
                    <button 
                        onClick={() => setShowRemarkModal(true)}
                        disabled={isAdding}
                        className="w-full py-3.5 premium-gradient text-white rounded-2xl font-bold shadow-xl shadow-indigo-100 hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                        {isAdding ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
                        加入“拟合仓”观察
                        <ChevronRight size={18} />
                    </button>
                )}
            </div>

            {/* Premium Remark Modal */}
            {showRemarkModal && (
                <div className="absolute inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm" onClick={() => { setShowRemarkModal(false); setRemarkText(''); setAddTradeMode('SIMULATED'); }}>
                    <div className="bg-white rounded-2xl shadow-2xl p-6 w-[360px] mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
                                <Plus size={16} className="text-indigo-500" /> 加入拟合仓
                            </h3>
                            <span className="text-[10px] text-slate-400 font-mono font-bold bg-slate-50 px-2 py-0.5 rounded border border-slate-100">
                                {stock.名称} {stock.代码}
                            </span>
                        </div>
                        {/* Trade Mode Selector */}
                        <div>
                            <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest block mb-1.5">
                                交易模式
                            </label>
                            <div className="flex bg-slate-100 rounded-xl p-1 gap-1">
                                <button
                                    onClick={() => setAddTradeMode('SIMULATED')}
                                    className={cn(
                                        "flex-1 py-2 text-xs font-black rounded-lg transition-all",
                                        addTradeMode === 'SIMULATED'
                                            ? "bg-gradient-to-r from-blue-500 to-indigo-500 text-white shadow-md"
                                            : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    🔵 模拟盘
                                </button>
                                <button
                                    onClick={() => setAddTradeMode('REAL')}
                                    className={cn(
                                        "flex-1 py-2 text-xs font-black rounded-lg transition-all",
                                        addTradeMode === 'REAL'
                                            ? "bg-gradient-to-r from-rose-500 to-red-500 text-white shadow-md"
                                            : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    🔴 实盘
                                </button>
                            </div>
                            {addTradeMode === 'REAL' && (
                                <p className="text-[10px] text-rose-500 font-bold mt-1.5 flex items-center gap-1">
                                    ⚠️ 实盘记录将标记为真实交易，请确认已实际买入
                                </p>
                            )}
                        </div>
                        <div>
                            <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest block mb-1.5">
                                持仓备注 / 交易纪律
                            </label>
                            <textarea
                                value={remarkText}
                                onChange={e => setRemarkText(e.target.value)}
                                placeholder="例如：均线共振突破，板块核心热点，中线布局..."
                                rows={3}
                                className="w-full px-3 py-2 text-sm text-slate-800 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-indigo-500 resize-none shadow-inner"
                                autoFocus
                                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAddToWatchlist(); } }}
                            />
                        </div>
                        <div className="flex justify-end gap-2 text-xs font-bold">
                            <button
                                onClick={() => { setShowRemarkModal(false); setRemarkText(''); setAddTradeMode('SIMULATED'); }}
                                className="px-4 py-2 text-slate-400 hover:text-slate-600 rounded-xl transition-all"
                            >
                                取消
                            </button>
                            <button
                                onClick={() => handleAddToWatchlist()}
                                disabled={isAdding}
                                className={cn(
                                    "px-4 py-2 text-white rounded-xl transition-all shadow-md flex items-center gap-1",
                                    addTradeMode === 'REAL'
                                        ? "bg-rose-600 hover:bg-rose-700 shadow-rose-100"
                                        : "bg-indigo-600 hover:bg-indigo-700 shadow-indigo-100"
                                )}
                            >
                                {isAdding && <Loader2 size={12} className="animate-spin" />}
                                {addTradeMode === 'REAL' ? '确认加入实盘' : '确认加入模拟'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Premium Sliding Toast Alert */}
            {toast && (
                <div className={cn(
                    "absolute top-4 left-4 right-4 z-50 px-4 py-3 rounded-2xl shadow-2xl text-xs font-bold border flex items-center gap-2 animate-in slide-in-from-top-4 duration-300",
                    toast.type === 'success' 
                        ? "bg-emerald-600 border-emerald-500 text-white shadow-emerald-100" 
                        : "bg-rose-600 border-rose-500 text-white shadow-rose-100"
                )}>
                    {toast.type === 'error' && <AlertTriangle size={14} />}
                    {toast.message}
                </div>
            )}
        </div>
    );
}
