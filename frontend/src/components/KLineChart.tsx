"use client";

import React, { useEffect, useMemo, useState } from 'react';
import api from '@/lib/api';
import { DOW_PHASE_LEGEND } from '@/lib/dowPhase';
import { Target, TrendingDown, TrendingUp, Zap } from 'lucide-react';
import SplitKLineCharts from './SplitKLineCharts';

interface KLineChartProps {
    code: string;
    name: string;
    strategyType?: string;
}

interface SignalData {
    buy_signals: Array<{
        time: string;
        price: number;
        reason: string;
    }>;
    sell_signals: Array<{
        time: string;
        price: number;
        reason: string;
        pnl_pct: number;
        hold_days: number;
    }>;
    trailing_stops?: Array<{
        time: string;
        value: number;
    }>;
}

export default function KLineChart({ code, name, strategyType = 'squeeze' }: KLineChartProps) {
    const [chartData, setChartData] = useState<any | null>(null);
    const [signalData, setSignalData] = useState<SignalData | null>(null);
    const [loading, setLoading] = useState(true);
    const [signalLoading, setSignalLoading] = useState(false);

    useEffect(() => {
        const fetchData = async () => {
            setLoading(true);
            try {
                const res = await api.get(`/api/kline/${code}?strategy_type=${strategyType}`);
                setChartData(res.data);

                setSignalLoading(true);
                try {
                    const sigRes = await api.get(`/api/stock/${code}/signals?strategy=${strategyType}`);
                    setSignalData(sigRes.data);
                } catch (e) {
                    console.warn("Signal fetch failed (non-critical):", e);
                } finally {
                    setSignalLoading(false);
                }
            } catch (e) {
                console.error("Failed to fetch kline data", e);
            } finally {
                setLoading(false);
            }
        };

        fetchData();
    }, [code, strategyType]);

    const totalBuys = signalData?.buy_signals?.length || 0;
    const totalSells = signalData?.sell_signals?.length || 0;
    const wins = signalData?.sell_signals?.filter(s => s.pnl_pct > 0).length || 0;
    const losses = signalData?.sell_signals?.filter(s => s.pnl_pct <= 0).length || 0;
    const winRate = totalSells > 0 ? ((wins / totalSells) * 100).toFixed(1) : '0';
    const avgReturn = totalSells > 0
        ? (signalData!.sell_signals.reduce((acc, s) => acc + s.pnl_pct, 0) / totalSells).toFixed(2)
        : '0';
    const avgHold = totalSells > 0
        ? (signalData!.sell_signals.reduce((acc, s) => acc + s.hold_days, 0) / totalSells).toFixed(1)
        : '0';

    const emaLines = useMemo(() => [
        { key: 'EMA20', label: 'EMA20', color: '#f59e0b' },
        { key: 'EMA120', label: 'EMA120', color: '#3b82f6' },
        { key: 'EMA250', label: 'EMA250', color: '#8b5cf6' },
    ], []);

    if (loading) {
        return (
            <div className="w-full h-[420px] flex items-center justify-center bg-slate-50 rounded-lg">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600" />
            </div>
        );
    }

    return (
        <div className="w-full h-full relative flex flex-col bg-white border border-slate-200 rounded-lg overflow-hidden">
            <div className="px-4 py-2 bg-slate-50 border-b border-slate-100 flex items-center justify-between">
                <div className="font-bold text-slate-800">
                    {name} <span className="text-slate-500 text-sm ml-2">{code}</span>
                </div>
                <div className="flex items-center gap-3 text-[10px] font-bold text-slate-500">
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-amber-500" />EMA20</span>
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-blue-500" />EMA120</span>
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-purple-500" />EMA250</span>
                    {(chartData.trend_phases || []).length > 0 && (
                        <span className="flex items-center gap-2 pl-2 border-l border-slate-200">
                            {DOW_PHASE_LEGEND.map((item) => (
                                <span key={item.label} className="flex items-center gap-1" title="道氏趋势阶段（K线上的方块标记，悬停查看操作建议）">
                                    <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: item.color }} />
                                    {item.label}
                                </span>
                            ))}
                        </span>
                    )}
                </div>
            </div>

            {chartData && (
                <SplitKLineCharts
                    candles={chartData.candlestick || []}
                    emaLines={emaLines}
                    rfFilter={chartData.rf_filter || []}
                    trailingStops={chartData.trailing_stops || signalData?.trailing_stops || []}
                    markers={chartData.markers || []}
                    buySignals={signalData?.buy_signals || []}
                    sellSignals={signalData?.sell_signals || []}
                    priceAction={chartData.price_action || null}
                    priceActionLines={chartData.price_action_lines || []}
                    trendPhases={chartData.trend_phases || []}
                    chartHints={chartData.chart_hints || []}
                    tradeProjection={chartData.trade_projection || null}
                    chipDistribution={chartData.chip_distribution || null}
                    height={400}
                />
            )}

            {totalBuys > 0 && !signalLoading && (
                <div className="px-4 py-3 bg-gradient-to-r from-slate-50 to-indigo-50/30 border-t border-slate-100 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-1.5">
                        <Zap size={12} className="text-indigo-500" />
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">回测信号总览</span>
                    </div>
                    <div className="flex items-center gap-5">
                        <div className="flex items-center gap-1.5">
                            <Target size={12} className="text-indigo-400" />
                            <span className="text-[10px] font-bold text-slate-500">胜率</span>
                            <span className={`text-sm font-black ${parseFloat(winRate) >= 50 ? 'text-rose-600' : 'text-slate-600'}`}>
                                {winRate}%
                            </span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <TrendingUp size={12} className="text-emerald-400" />
                            <span className="text-[10px] font-bold text-slate-500">均收</span>
                            <span className={`text-sm font-black ${parseFloat(avgReturn) >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                                {parseFloat(avgReturn) >= 0 ? '+' : ''}{avgReturn}%
                            </span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <TrendingDown size={12} className="text-amber-400" />
                            <span className="text-[10px] font-bold text-slate-500">均持</span>
                            <span className="text-sm font-black text-slate-600">{avgHold}天</span>
                        </div>
                        <div className="flex items-center gap-2 ml-2 pl-2 border-l border-slate-200">
                            <span className="text-[9px] font-bold text-emerald-500 bg-emerald-50 px-1.5 py-0.5 rounded">赢 {wins}</span>
                            <span className="text-[9px] font-bold text-rose-500 bg-rose-50 px-1.5 py-0.5 rounded">亏 {losses}</span>
                        </div>
                    </div>
                </div>
            )}
            {signalLoading && (
                <div className="px-4 py-2 bg-slate-50 border-t border-slate-100 text-center">
                    <span className="text-[10px] font-bold text-slate-400 animate-pulse">正在加载回测信号...</span>
                </div>
            )}
        </div>
    );
}
