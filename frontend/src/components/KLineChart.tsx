"use client";

import React, { useEffect, useRef } from 'react';
import {
    createChart,
    ColorType,
    IChartApi,
    CandlestickSeries,
    LineSeries
} from 'lightweight-charts';
import api from '@/lib/api';

interface KLineChartProps {
    code: string;
    name: string;
}

export default function KLineChart({ code, name }: KLineChartProps) {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

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
    }, [code]);

    return (
        <div className="w-full h-full relative">
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
            </div>
            <div ref={chartContainerRef} className="w-full h-full" />
        </div>
    );
}
