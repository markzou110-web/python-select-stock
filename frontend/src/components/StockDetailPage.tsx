"use client";

import React, { useEffect, useState, useRef } from 'react';
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
    PlusCircle
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
    createChart,
    ColorType,
    CandlestickSeries,
    LineSeries,
    IChartApi,
    createSeriesMarkers
} from 'lightweight-charts';
import api from '@/lib/api';

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
}

export default function StockDetailPage({ code, name, onBack }: StockDetailPageProps) {
    const [data, setData] = useState<FullAnalysisData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        const fetchData = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await api.get(`/api/stock/full-analysis?code=${code}`);
                setData(res.data);
            } catch (err: any) {
                console.error("Full analysis fetch error:", err);
                setError(err.response?.data?.detail || '数据加载失败');
            } finally {
                setLoading(false);
            }
        };
        fetchData();
    }, [code]);

    // Chart rendering
    useEffect(() => {
        if (!chartContainerRef.current || !data || data.kline.length === 0) return;

        // Clear previous chart
        if (chartRef.current) {
            chartRef.current.remove();
            chartRef.current = null;
        }

        const isPaperTrade = data.stock_info?.is_paper_trade;

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#64748b',
            },
            grid: {
                vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
                horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
            },
            width: chartContainerRef.current.clientWidth,
            height: 500,
            timeScale: {
                borderColor: 'rgba(148, 163, 184, 0.15)',
                timeVisible: false,
            },
            rightPriceScale: {
                borderColor: 'rgba(148, 163, 184, 0.15)',
                scaleMargins: { top: 0.08, bottom: 0.08 },
            },
            crosshair: {
                vertLine: { color: 'rgba(99, 102, 241, 0.3)', width: 1 },
                horzLine: { color: 'rgba(99, 102, 241, 0.3)', width: 1 },
            },
        });

        const candleSeries = chart.addSeries(CandlestickSeries, {
            upColor: '#ef4444',
            downColor: '#22c55e',
            borderVisible: false,
            wickUpColor: '#ef4444',
            wickDownColor: '#22c55e',
        });

        const ema5 = chart.addSeries(LineSeries, { color: '#6366f1', lineWidth: 1, title: 'EMA5' });
        const ema20 = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, title: 'EMA20' });
        const ema60 = chart.addSeries(LineSeries, { color: '#8b5cf6', lineWidth: 1, title: 'EMA60' });

        candleSeries.setData(data.kline);
        ema5.setData(data.kline.map(d => ({ time: d.time, value: d.EMA5 })));
        ema20.setData(data.kline.map(d => ({ time: d.time, value: d.EMA20 })));
        ema60.setData(data.kline.map(d => ({ time: d.time, value: d.EMA60 })));

        // Signal markers
        if (data.signals?.buy_signals || data.signals?.sell_signals) {
            const markers: any[] = [];
            if (data.signals.buy_signals) {
                for (const sig of data.signals.buy_signals) {
                    markers.push({
                        time: sig.time,
                        position: 'belowBar',
                        color: '#ef4444',
                        shape: 'arrowUp',
                        text: 'B',
                    });
                }
            }
            if (data.signals.sell_signals) {
                for (const sig of data.signals.sell_signals) {
                    markers.push({
                        time: sig.time,
                        position: 'aboveBar',
                        color: sig.pnl_pct >= 0 ? '#22c55e' : '#f59e0b',
                        shape: 'arrowDown',
                        text: sig.pnl_pct >= 0 ? `+${sig.pnl_pct}%` : `${sig.pnl_pct}%`,
                    });
                }
            }
            markers.sort((a, b) => a.time.localeCompare(b.time));
            if (markers.length > 0) {
                const markersPlugin = createSeriesMarkers(candleSeries);
                markersPlugin.setMarkers(markers);
            }
        }

        // Trailing stop line
        if (data.signals?.trailing_stops) {
            const trailingSeries = chart.addSeries(LineSeries, {
                color: '#f97316', lineWidth: 2, lineStyle: 2,
                lastValueVisible: false, priceLineVisible: false
            });
            trailingSeries.setData(data.signals.trailing_stops);
        }

        // Paper trade price lines
        if (isPaperTrade) {
            candleSeries.createPriceLine({
                price: data.stock_info.buy_price,
                color: '#6366f1',
                lineWidth: 2,
                lineStyle: 2,
                axisLabelVisible: true,
                title: '买入价',
            });
            candleSeries.createPriceLine({
                price: data.stock_info.stop_price,
                color: '#f43f5e',
                lineWidth: 2,
                lineStyle: 2,
                axisLabelVisible: true,
                title: '止损价',
            });
            candleSeries.createPriceLine({
                price: data.stock_info.take_profit_price,
                color: '#10b981',
                lineWidth: 2,
                lineStyle: 2,
                axisLabelVisible: true,
                title: '止盈价',
            });
        }

        chart.timeScale().fitContent();
        chartRef.current = chart;

        const handleResize = () => {
            if (chartContainerRef.current) {
                chart.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, [data]);

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
                {info.is_paper_trade && (
                    <div className="flex items-center gap-2 px-4 py-2 bg-indigo-50 border border-indigo-200 rounded-2xl shadow-sm">
                        <div className="w-2 h-2 rounded-full bg-indigo-500 animate-ping" />
                        <span className="text-xs font-black text-indigo-600 uppercase tracking-widest">拟合实盘持仓中</span>
                    </div>
                )}
            </div>

            {/* ═══ Full-Width K-Line Chart ═══ */}
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
                                    <span className="text-[10px] font-bold text-rose-500">止损价</span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <div className="w-3 h-0.5 bg-emerald-500 border-t border-dashed border-emerald-500" />
                                    <span className="text-[10px] font-bold text-emerald-500">止盈价</span>
                                </div>
                            </>
                        )}
                    </div>
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">200日K线</span>
                </div>
                <div ref={chartContainerRef} className="w-full h-[500px]" />
            </div>

            {/* ═══ Trading Metrics Strip ═══ */}
            {info.is_paper_trade && (
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    <MetricCard label="买入均价" value={`¥${info.buy_price.toFixed(2)}`} color="text-indigo-600" icon={<DollarSign size={14} />} />
                    <MetricCard label="移动止损" value={`¥${info.stop_price.toFixed(2)}`} color="text-rose-600" icon={<ShieldAlert size={14} />} />
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
