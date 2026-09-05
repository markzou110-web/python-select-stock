"use client";

import React, { useEffect, useRef } from 'react';
import {
    AreaSeries,
    CandlestickSeries,
    ColorType,
    createChart,
    type UTCTimestamp,
} from 'lightweight-charts';

export interface HotStockChartPoint {
    time: string;
    open: number;
    close: number;
    high: number;
    low: number;
    volume: number;
}

interface HotStockChartProps {
    period: 'minute' | 'day';
    points: HotStockChartPoint[];
    previousClose?: number;
}

function minuteTimestamp(value: string): UTCTimestamp {
    return Math.floor(new Date(`${value.replace(' ', 'T')}+08:00`).getTime() / 1000) as UTCTimestamp;
}

export default function HotStockChart({ period, points, previousClose = 0 }: HotStockChartProps) {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const container = containerRef.current;
        if (!container || points.length === 0) return;

        const chart = createChart(container, {
            width: container.clientWidth,
            height: container.clientHeight,
            layout: {
                background: { type: ColorType.Solid, color: '#ffffff' },
                textColor: '#64748b',
                fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            },
            grid: {
                vertLines: { color: '#f1f5f9' },
                horzLines: { color: '#f1f5f9' },
            },
            rightPriceScale: { borderColor: '#e2e8f0' },
            timeScale: {
                borderColor: '#e2e8f0',
                timeVisible: period === 'minute',
                secondsVisible: false,
                rightOffset: 2,
            },
            crosshair: {
                vertLine: { color: '#94a3b8', labelBackgroundColor: '#334155' },
                horzLine: { color: '#94a3b8', labelBackgroundColor: '#334155' },
            },
            localization: { locale: 'zh-CN' },
        });

        if (period === 'minute') {
            const areaSeries = chart.addSeries(AreaSeries, {
                lineColor: '#0f766e',
                topColor: 'rgba(13, 148, 136, 0.24)',
                bottomColor: 'rgba(13, 148, 136, 0.02)',
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: true,
            });
            areaSeries.setData(points.map((point) => ({
                time: minuteTimestamp(point.time),
                value: point.close,
            })));
            if (previousClose > 0) {
                areaSeries.createPriceLine({
                    price: previousClose,
                    color: '#94a3b8',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: true,
                    title: '昨收',
                });
            }
        } else {
            const candleSeries = chart.addSeries(CandlestickSeries, {
                upColor: '#dc2626',
                downColor: '#0f766e',
                wickUpColor: '#dc2626',
                wickDownColor: '#0f766e',
                borderVisible: false,
                priceLineVisible: false,
            });
            candleSeries.setData(points.map((point) => ({
                time: point.time,
                open: point.open,
                high: point.high,
                low: point.low,
                close: point.close,
            })));
        }

        chart.timeScale().fitContent();
        const observer = new ResizeObserver(() => {
            chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
        });
        observer.observe(container);
        return () => {
            observer.disconnect();
            chart.remove();
        };
    }, [period, points, previousClose]);

    return <div ref={containerRef} className="h-[360px] w-full sm:h-[460px] xl:h-[540px]" aria-label={period === 'minute' ? '个股分时走势图' : '个股日 K 走势图'} />;
}
