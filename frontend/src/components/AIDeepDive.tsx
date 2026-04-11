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
    Loader2
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

interface AIDeepDiveProps {
    stock: any;
    onClose: () => void;
}

export default function AIDeepDive({ stock, onClose }: AIDeepDiveProps) {
    const [radarData, setRadarData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [chartData, setChartData] = useState<any[]>([]);
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        const fetchData = async () => {
            setLoading(true);
            try {
                const res = await api.get(`/api/stock/detail?code=${stock.代码}`);
                const data = res.data.data;
                setChartData(data);

                // Radar mapping
                setRadarData([
                    { subject: '趋势', A: Math.min(stock.Score / 1.5, 100), fullMark: 100 },
                    { subject: '动能', A: stock.RSI || 60, fullMark: 100 },
                    { subject: '量能', A: (stock.Score % 30) * 3, fullMark: 100 },
                    { subject: '板块', A: stock.共振 === "🔥 核心热点" ? 95 : 60, fullMark: 100 },
                    { subject: '资金', A: stock.北向?.includes("流入") ? 90 : 50, fullMark: 100 },
                    { subject: '形态', A: (1 - (stock.影线比 || 0)) * 100, fullMark: 100 },
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
            height: 180,
            timeScale: {
                borderVisible: false,
                visible: false
            },
            rightPriceScale: {
                borderVisible: false,
                scaleMargins: {
                    top: 0.1,
                    bottom: 0.1,
                },
            },
            handleScroll: false,
            handleScale: false,
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

        chart.timeScale().fitContent();
        chartRef.current = chart;

        return () => {
            chart.remove();
        };
    }, [chartData]);

    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-2xl h-full flex flex-col overflow-hidden">
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
                    <div className="h-[180px] bg-slate-50/50 rounded-2xl flex items-center justify-center border border-slate-100 relative group overflow-hidden">
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
                <button className="w-full py-3.5 premium-gradient text-white rounded-2xl font-bold shadow-xl shadow-indigo-100 hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center justify-center gap-2">
                    加入“拟合仓”观察
                    <ChevronRight size={18} />
                </button>
            </div>
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
            <div className="text-lg font-black text-slate-900">{value}</div>
        </div>
    );
}
