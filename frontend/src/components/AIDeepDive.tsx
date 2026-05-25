"use client";

import React, { useEffect, useState, useRef } from 'react';
import {
    X,
    Zap,
    TrendingUp,
    ShieldCheck,
    Activity,
    Info,
    ChevronRight,
    Target,
    BarChart3,
    History,
    Loader2,
    Plus,
    AlertTriangle
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
    Radar,
    RadarChart,
    PolarGrid,
    PolarAngleAxis,
    ResponsiveContainer
} from 'recharts';
import {
    createChart,
    ColorType,
    CandlestickSeries,
    LineSeries,
    IChartApi
} from 'lightweight-charts';
import api from '@/lib/api';
import { useTradeStore } from '@/stores/tradeStore';

interface AIDeepDiveProps {
    stock: any;
    onClose: () => void;
}

export default function AIDeepDive({ stock, onClose }: AIDeepDiveProps) {
    const [radarData, setRadarData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [chartData, setChartData] = useState<any[]>([]);
    const [stockInfo, setStockInfo] = useState<any>(null);
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    // Simulated trading addition states
    const [showRemarkModal, setShowRemarkModal] = useState(false);
    const [remarkText, setRemarkText] = useState('');
    const [isAdding, setIsAdding] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 2500);
    };

    useEffect(() => {
        const fetchData = async () => {
            setLoading(true);
            try {
                const res = await api.get(`/api/stock/detail?code=${stock.代码}`);
                const data = res.data.data;
                setChartData(data);

                // Use API-returned stock_info values if available, fallback to props
                const info = res.data.stock_info || stock;
                setStockInfo(info);

                // Radar mapping
                setRadarData([
                    { subject: '趋势', A: Math.min((info.Score || 60) / 1.5, 100), fullMark: 100 },
                    { subject: '动能', A: info.RSI || 60, fullMark: 100 },
                    { subject: '量能', A: ((info.Score || 60) % 30) * 3, fullMark: 100 },
                    { subject: '板块', A: info.共振 === "🔥 核心热点" ? 95 : 60, fullMark: 100 },
                    { subject: '资金', A: info.北向?.includes("流入") ? 90 : 50, fullMark: 100 },
                    { subject: '形态', A: (1 - (info.影线比 || 0)) * 100, fullMark: 100 },
                ]);
            } catch (err) {
                console.error("Deep Dive Fetch Error:", err);
            } finally {
                setLoading(false);
            }
        };

        fetchData();
    }, [stock]);

    useEffect(() => {
        if (!chartContainerRef.current || chartData.length === 0) return;

        const isPaperTrade = stockInfo?.is_paper_trade;

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#94a3b8',
            },
            grid: {
                vertLines: { visible: false },
                horzLines: { color: 'rgba(148, 163, 184, 0.05)' },
            },
            width: chartContainerRef.current.clientWidth,
            height: isPaperTrade ? 220 : 180,
            timeScale: {
                borderVisible: false,
                visible: isPaperTrade ? true : false
            },
            rightPriceScale: {
                visible: isPaperTrade ? true : false,
                borderVisible: false,
                scaleMargins: {
                    top: 0.15,
                    bottom: 0.15,
                },
            },
            handleScroll: isPaperTrade ? true : false,
            handleScale: isPaperTrade ? true : false,
        });

        const candlestickSeries = chart.addSeries(CandlestickSeries, {
            upColor: '#ef4444',
            downColor: '#22c55e',
            borderVisible: false,
            wickUpColor: '#ef4444',
            wickDownColor: '#22c55e',
        });

        const ema5Series = chart.addSeries(LineSeries, { color: '#6366f1', lineWidth: 1 });
        const ema20Series = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1 });

        candlestickSeries.setData(chartData);
        ema5Series.setData(chartData.map(d => ({ time: d.time, value: d.EMA5 })));
        ema20Series.setData(chartData.map(d => ({ time: d.time, value: d.EMA20 })));

        if (isPaperTrade) {
            candlestickSeries.createPriceLine({
                price: stockInfo.buy_price,
                color: '#6366f1',
                lineWidth: 2,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: '买入价',
            });
            candlestickSeries.createPriceLine({
                price: stockInfo.stop_price,
                color: '#f43f5e',
                lineWidth: 2,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: '止损价',
            });
            candlestickSeries.createPriceLine({
                price: stockInfo.take_profit_price,
                color: '#10b981',
                lineWidth: 2,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: '止盈价',
            });
        }

        chart.timeScale().fitContent();
        chartRef.current = chart;

        return () => {
            chart.remove();
        };
    }, [chartData, stockInfo]);

    const handleAddToWatchlist = async (force: boolean = false, customRemark?: string) => {
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
                force: force
            });

            if (res.data.status === 'success') {
                showToast(`${stock.名称} 已成功加入模拟实盘！`);
                setShowRemarkModal(false);
                setRemarkText('');
                // Automatically refresh simulated portfolio state
                useTradeStore.getState().fetchTrades();
            } else if (res.data.status === 'warning') {
                // Trigger bypass option
                if (window.confirm(res.data.detail)) {
                    await handleAddToWatchlist(true, customRemark);
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
                                <p className="text-[9px] font-black text-rose-500 tracking-wider mb-0.5">移动止损</p>
                                <p className="text-sm font-extrabold text-rose-600 font-mono">¥{stockInfo.stop_price.toFixed(2)}</p>
                                <span className="text-[8px] font-bold text-rose-400/80 block mt-0.5">
                                    {(100 * (stockInfo.现价 - stockInfo.stop_price) / stockInfo.现价).toFixed(1)}% 缓冲
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
                    </div>
                )}

                {/* 1. Radar View */}
                <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <Activity size={16} className="text-indigo-500" />
                        <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">六维实力雷达</h4>
                    </div>
                    <div className="h-64 w-full bg-slate-50/50 rounded-2xl border border-slate-100 p-4">
                        <ResponsiveContainer width="100%" height="100%">
                            <RadarChart cx="50%" cy="50%" outerRadius="80%" data={radarData}>
                                <PolarGrid stroke="#e2e8f0" />
                                <PolarAngleAxis dataKey="subject" tick={{ fill: '#94a3b8', fontSize: 10, fontWeight: 800 }} />
                                <Radar
                                    name="Score"
                                    dataKey="A"
                                    stroke="#6366f1"
                                    fill="#6366f1"
                                    fillOpacity={0.6}
                                />
                            </RadarChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* 2. AI Narrative Summary */}
                <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <ShieldCheck size={16} className="text-emerald-500" />
                        <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">AI 智能简报</h4>
                    </div>
                    <div className="bg-indigo-600 rounded-2xl p-5 text-white shadow-lg shadow-indigo-100 relative overflow-hidden">
                        <p className="text-sm font-medium leading-relaxed relative z-10">
                            "{stock.名称} 今日展现极强强度，综合得分 <span className="font-black text-amber-300 font-mono">{stock.Score?.toFixed(1) || 'N/A'}</span>。
                            所属 <span className="px-1.5 py-0.5 bg-white/20 rounded-lg text-xs font-bold">【{stock.行业 || '未知'}】</span>
                            {stock.共振 === "🔥 核心热点" ? "处于板块强势共振中。" : "个股独立活跃。"}
                            {stock.北向?.includes("流入") ? "北向资金近期持续加仓，资金面健康。" : "资金融入度一般，需关注量能持续性。"}
                            {stock.粘合度 != null ? (
                                <>均线粘合度 <span className="font-bold underline decoration-indigo-300 underline-offset-4">{stock.粘合度.toFixed(4)}</span>，</>
                            ) : stock.体质 ? (
                                <>沉积体质 <span className="font-bold underline decoration-indigo-300 underline-offset-4">{stock.体质}</span>，</>
                            ) : null}
                            属于典型的高胜率共振突破模型。"
                        </p>
                        <div className="absolute -bottom-4 -right-4 w-24 h-24 bg-white/10 rounded-full blur-2xl" />
                    </div>
                </div>

                {/* 3. Indicators Grid */}
                <div className="grid grid-cols-2 gap-4">
                    <IndicatorCard
                        icon={<TrendingUp size={14} />}
                        label="RSI 强度"
                        value={stock.RSI}
                        color="text-indigo-500"
                    />
                    <IndicatorCard
                        icon={<History size={14} />}
                        label="历史胜率"
                        value={stock.历史胜率}
                        color="text-emerald-500"
                    />
                    <IndicatorCard
                        icon={<Target size={14} />}
                        label={stock.体质 ? "沉积体质" : "粘合位"}
                        value={stock.粘合度 != null ? stock.粘合度.toFixed(3) : stock.体质 || "N/A"}
                        color="text-amber-500"
                    />
                    <IndicatorCard
                        icon={<BarChart3 size={14} />}
                        label="上影比"
                        value={stock.影线比?.toFixed(2) || "0.00"}
                        color="text-rose-500"
                    />
                </div>

                {/* 3.5 Backtest Stats */}
                {stock.回测统计 && stock.回测统计.avg_return !== 0 && (
                    <div className="space-y-3">
                        <div className="flex items-center gap-2">
                            <BarChart3 size={16} className="text-amber-500" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">回测概览</h4>
                        </div>
                        <div className="grid grid-cols-3 gap-3">
                            <div className="p-3 bg-white border border-slate-100 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400 uppercase">平均收益</p>
                                <p className={`text-lg font-black ${stock.回测统计.avg_return >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                                    {stock.回测统计.avg_return >= 0 ? '+' : ''}{stock.回测统计.avg_return}%
                                </p>
                            </div>
                            <div className="p-3 bg-white border border-slate-100 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400 uppercase">最大回撤</p>
                                <p className="text-lg font-black text-emerald-600">{stock.回测统计.max_drawdown}%</p>
                            </div>
                            <div className="p-3 bg-white border border-slate-100 rounded-xl text-center">
                                <p className="text-[9px] font-bold text-slate-400 uppercase">盈亏比</p>
                                <p className="text-lg font-black text-amber-600">{stock.回测统计.profit_factor}</p>
                            </div>
                        </div>
                        <div className="flex justify-between px-2">
                            <span className="text-[9px] font-bold text-slate-400">
                                平均持仓 {stock.回测统计.avg_hold_days} 天
                            </span>
                            {stock.回测统计.stop_loss_hits > 0 && (
                                <span className="text-[9px] font-bold text-rose-400">
                                    触发止损 {stock.回测统计.stop_loss_hits} 次
                                </span>
                            )}
                        </div>
                    </div>
                )}

                {/* 4. Mini Chart */}
                <div className="space-y-3">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Info size={16} className="text-slate-400" />
                            <h4 className="text-xs font-black text-slate-400 uppercase tracking-widest">60日趋势预览 (Mini)</h4>
                        </div>
                    </div>
                    <div className={cn(
                        "bg-slate-50/50 rounded-2xl flex items-center justify-center border border-slate-100 relative group overflow-hidden",
                        stockInfo?.is_paper_trade ? "h-[220px]" : "h-[180px]"
                    )}>
                        {loading ? (
                            <Loader2 className="animate-spin text-slate-300" size={24} />
                        ) : (
                            <div ref={chartContainerRef} className="w-full h-full" />
                        )}
                        <div className="absolute top-2 left-2 z-10 flex gap-2">
                            <span className="text-[9px] font-bold px-1.5 py-0.5 bg-indigo-50 text-indigo-500 rounded-md">EMA5/20</span>
                        </div>
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
                <div className="absolute inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm" onClick={() => { setShowRemarkModal(false); setRemarkText(''); }}>
                    <div className="bg-white rounded-2xl shadow-2xl p-6 w-[360px] mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
                                <Plus size={16} className="text-indigo-500" /> 加入拟合仓
                            </h3>
                            <span className="text-[10px] text-slate-400 font-mono font-bold bg-slate-50 px-2 py-0.5 rounded border border-slate-100">
                                {stock.名称} {stock.代码}
                            </span>
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
                                onClick={() => { setShowRemarkModal(false); setRemarkText(''); }}
                                className="px-4 py-2 text-slate-400 hover:text-slate-600 rounded-xl transition-all"
                            >
                                取消
                            </button>
                            <button
                                onClick={() => handleAddToWatchlist()}
                                disabled={isAdding}
                                className="px-4 py-2 text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl transition-all shadow-md shadow-indigo-100 flex items-center gap-1"
                            >
                                {isAdding && <Loader2 size={12} className="animate-spin" />}
                                确认加入
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

function IndicatorCard({ icon, label, value, color }: { icon: any, label: string, value: any, color: string }) {
    return (
        <div className="p-4 bg-white border border-slate-100 rounded-2xl shadow-sm">
            <div className="flex items-center gap-2 mb-2">
                <span className={cn("p-1.5 rounded-lg bg-slate-50", color)}>{icon}</span>
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</span>
            </div>
            <div className="text-lg font-black text-slate-900">{value !== undefined && value !== null ? value : "---"}</div>
        </div>
    );
}
