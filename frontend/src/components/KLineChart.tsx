"use client";

import React, { useEffect, useRef, useState } from 'react';
import {
    createChart,
    ColorType,
    IChartApi,
    CandlestickSeries,
    LineSeries
} from 'lightweight-charts';
import api from '@/lib/api';
import { TrendingUp, TrendingDown, Target, Zap } from 'lucide-react';

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
}

export default function KLineChart({ code, name, strategyType = 'squeeze' }: KLineChartProps) {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const [signalData, setSignalData] = useState<SignalData | null>(null);
    const [signalLoading, setSignalLoading] = useState(false);

    useEffect(() => {
        if (!chartContainerRef.current) return;

        const handleResize = () => {
            chartRef.current?.applyOptions({ width: chartContainerRef.current?.clientWidth });
        };

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#64748b',
            },
            grid: {
                vertLines: { color: 'rgba(148, 163, 184, 0.1)' },
                horzLines: { color: 'rgba(148, 163, 184, 0.1)' },
            },
            width: chartContainerRef.current.clientWidth,
            height: 400,
            timeScale: {
                borderColor: 'rgba(148, 163, 184, 0.2)',
            },
        });

        const candlestickSeries = chart.addSeries(CandlestickSeries, {
            upColor: '#ef4444',
            downColor: '#22c55e',
            borderVisible: false,
            wickUpColor: '#ef4444',
            wickDownColor: '#22c55e',
        });

        const ema20Series = chart.addSeries(LineSeries, { color: '#f59e0b', lineWidth: 1, title: 'EMA20' });
        const ema120Series = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 1, title: 'EMA120' });
        const ema250Series = chart.addSeries(LineSeries, { color: '#8b5cf6', lineWidth: 1, title: 'EMA250' });

        chartRef.current = chart;

        const fetchData = async () => {
            try {
                const res = await api.get(`/api/stock/${code}/kline?local_only=true`);
                const rawData = res.data.data;

                if (rawData && rawData.length > 0) {
                    candlestickSeries.setData(rawData);

                    ema20Series.setData(rawData.map((d: any) => ({ time: d.time, value: d.EMA20 })));
                    ema120Series.setData(rawData.map((d: any) => ({ time: d.time, value: d.EMA120 })));
                    ema250Series.setData(rawData.map((d: any) => ({ time: d.time, value: d.EMA250 })));

                    chart.timeScale().fitContent();
                }

                // Fetch buy/sell signals for overlay
                setSignalLoading(true);
                try {
                    const sigRes = await api.get(`/api/stock/${code}/signals?strategy=${strategyType}`);
                    const signals: SignalData = sigRes.data;
                    setSignalData(signals);

                    // Build markers array for lightweight-charts
                    const markers: any[] = [];

                    if (signals.buy_signals) {
                        for (const sig of signals.buy_signals) {
                            markers.push({
                                time: sig.time,
                                position: 'belowBar',
                                color: '#ef4444',
                                shape: 'arrowUp',
                                text: 'B',
                            });
                        }
                    }

                    if (signals.sell_signals) {
                        for (const sig of signals.sell_signals) {
                            markers.push({
                                time: sig.time,
                                position: 'aboveBar',
                                color: sig.pnl_pct >= 0 ? '#22c55e' : '#f59e0b',
                                shape: 'arrowDown',
                                text: sig.pnl_pct >= 0 ? `+${sig.pnl_pct}%` : `${sig.pnl_pct}%`,
                            });
                        }
                    }

                    // Sort markers by time (required by lightweight-charts)
                    markers.sort((a, b) => a.time.localeCompare(b.time));

                    if (markers.length > 0) {
                        candlestickSeries.setMarkers(markers);
                    }
                } catch (e) {
                    console.warn("Signal fetch failed (non-critical):", e);
                } finally {
                    setSignalLoading(false);
                }
            } catch (e) {
                console.error("Failed to fetch kline data", e);
            }
        };

        fetchData();

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, [code, strategyType]);

    // Compute signal stats
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

    return (
        <div className="w-full h-full relative flex flex-col">
            <div className="absolute top-4 left-6 z-10 flex gap-4">
                <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                    <span className="text-[10px] font-bold text-slate-500">EMA 20</span>
                </div>
                <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-blue-500" />
                    <span className="text-[10px] font-bold text-slate-500">EMA 120</span>
                </div>
                <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-purple-500" />
                    <span className="text-[10px] font-bold text-slate-500">EMA 250</span>
                </div>
                {totalBuys > 0 && (
                    <>
                        <div className="w-px h-4 bg-slate-200" />
                        <div className="flex items-center gap-1.5">
                            <div className="w-0 h-0 border-l-[4px] border-r-[4px] border-b-[6px] border-transparent border-b-red-500" />
                            <span className="text-[10px] font-bold text-rose-500">买入 B ({totalBuys})</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <div className="w-0 h-0 border-l-[4px] border-r-[4px] border-t-[6px] border-transparent border-t-emerald-500" />
                            <span className="text-[10px] font-bold text-emerald-500">卖出 S ({totalSells})</span>
                        </div>
                    </>
                )}
            </div>
            <div ref={chartContainerRef} className="w-full flex-1" />

            {/* Signal Summary Panel */}
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
