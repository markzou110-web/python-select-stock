"use client";

import React, { useEffect, useMemo, useRef } from 'react';
import {
    createChart,
    ColorType,
    CandlestickSeries,
    LineSeries,
    createSeriesMarkers,
    IChartApi,
} from 'lightweight-charts';
import { AlertTriangle, CheckCircle2, ShieldAlert, TrendingUp } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SplitKLineChartsProps {
    candles: any[];
    emaLines?: { key: string; label: string; color: string; width?: number }[];
    rfFilter?: any[];
    trailingStops?: any[];
    markers?: any[];
    buySignals?: any[];
    sellSignals?: any[];
    priceAction?: any;
    priceActionLines?: any[];
    paperLines?: { price: number; label: string; color: string; date?: string }[];
    riskLevels?: any;
    height?: number;
    compact?: boolean;
}

function validNumber(value: any) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
}

function normalizeCandles(candles: any[]) {
    const map = new Map<string, any>();
    candles.forEach((item) => {
        if (!item?.time) return;
        const open = validNumber(item.open);
        const high = validNumber(item.high);
        const low = validNumber(item.low);
        const close = validNumber(item.close);
        if (open == null || high == null || low == null || close == null) return;
        map.set(String(item.time), { ...item, time: String(item.time), open, high, low, close });
    });
    return Array.from(map.values()).sort((a, b) => a.time.localeCompare(b.time));
}

function normalizeLine(data: any[] | undefined, valueKey = 'value') {
    const map = new Map<string, number>();
    (data || []).forEach((item) => {
        if (!item?.time) return;
        const value = validNumber(item[valueKey]);
        if (value == null) return;
        map.set(String(item.time), value);
    });
    return Array.from(map.entries())
        .map(([time, value]) => ({ time, value }))
        .sort((a, b) => a.time.localeCompare(b.time));
}

function buildSignalMarkers(buySignals?: any[], sellSignals?: any[]) {
    const markers: any[] = [];
    (buySignals || []).forEach((sig) => {
        if (!sig?.time) return;
        markers.push({
            time: String(sig.time),
            position: 'belowBar',
            color: '#ef4444',
            shape: 'arrowUp',
            text: 'B',
        });
    });
    (sellSignals || []).forEach((sig) => {
        if (!sig?.time) return;
        const pnl = validNumber(sig.pnl_pct);
        markers.push({
            time: String(sig.time),
            position: 'aboveBar',
            color: pnl != null && pnl >= 0 ? '#22c55e' : '#f59e0b',
            shape: 'arrowDown',
            text: pnl != null ? `${pnl >= 0 ? '+' : ''}${pnl}%` : 'S',
        });
    });
    return markers.sort((a, b) => a.time.localeCompare(b.time));
}

function buildStrategySummary(candles: any[], trailingStops?: any[], buySignals?: any[], sellSignals?: any[]) {
    if (candles.length === 0) {
        return {
            title: 'K线数据不足',
            body: '当前没有可用 K 线，先补齐本地行情数据后再判断趋势和风控位。',
            tone: 'neutral' as const,
        };
    }

    const last = candles[candles.length - 1];
    const close = Number(last.close);
    const ema20 = validNumber(last.EMA20);
    const ema60 = validNumber(last.EMA60);
    const trailing = normalizeLine(trailingStops).at(-1)?.value;
    const stopGap = trailing ? ((close - trailing) / close) * 100 : null;
    const aboveEma20 = ema20 != null && close >= ema20;
    const aboveEma60 = ema60 != null && close >= ema60;
    const recentBuys = (buySignals || []).filter((s) => s?.time && s.time >= candles[Math.max(0, candles.length - 20)]?.time).length;
    const recentSells = (sellSignals || []).filter((s) => s?.time && s.time >= candles[Math.max(0, candles.length - 20)]?.time).length;

    if (stopGap != null && stopGap < 2) {
        return {
            title: '风控距离偏近',
            body: `现价距离移动风控线约 ${stopGap.toFixed(1)}%，后续以保护利润为先，跌破风控线不宜硬扛。`,
            tone: 'risk' as const,
        };
    }

    if (aboveEma20 && aboveEma60) {
        return {
            title: '趋势仍偏多',
            body: `收盘价站在中期均线之上，策略图更适合继续跟踪持仓。若放量突破前高，可考虑加仓；若跌回 EMA20 下方，降低仓位。${recentBuys > recentSells ? '近端买入信号多于卖出信号，趋势延续性较好。' : ''}`,
            tone: 'positive' as const,
        };
    }

    if (ema20 != null && close < ema20) {
        return {
            title: '短线转弱',
            body: '收盘价落在 EMA20 下方，先按防守处理。后续需要重新站回均线并伴随量能恢复，再考虑恢复进攻仓位。',
            tone: 'risk' as const,
        };
    }

    return {
        title: '趋势待确认',
        body: '当前 K 线没有给出足够清晰的趋势方向，建议等待突破、回踩确认或新的策略买卖点出现。',
        tone: 'neutral' as const,
    };
}

function buildPriceActionSummary(priceAction: any, priceActionLines?: any[]) {
    const score = validNumber(priceAction?.price_action_score);
    const summary = priceAction?.price_action_summary;
    const regime = priceAction?.price_action_regime;
    const quality = priceAction?.price_action_entry_quality;

    if (summary) {
        const action = score != null && score >= 70
            ? '结构评分较高，若右图入场线被放量突破，可小仓试错并严格按失效位止损。'
            : score != null && score < 55
                ? '结构优势不足，优先等待更明确的二次突破或回踩确认。'
                : '结构处在可观察区间，适合等待价格靠近入场线或失效线后再做决策。';
        return {
            title: `${regime || '价格行为'}${quality ? ` · ${quality}` : ''}`,
            body: `${summary} ${action}`,
            tone: score != null && score >= 70 ? 'positive' as const : score != null && score < 55 ? 'risk' as const : 'neutral' as const,
        };
    }

    if ((priceActionLines || []).length > 0) {
        return {
            title: '结构线已生成',
            body: '右图展示趋势线、入场线和失效线。后续重点观察价格是否沿趋势线推进，跌破失效线则降低仓位或退出观察。',
            tone: 'neutral' as const,
        };
    }

    return {
        title: '价格行为暂不清晰',
        body: '当前没有识别到足够稳定的价格行为结构。建议先看左图趋势和风控线，等待右图出现明确入场/失效结构。',
        tone: 'neutral' as const,
    };
}

function formatPrice(value: number | null | undefined) {
    return value != null && Number.isFinite(value) && value > 0 ? value.toFixed(2) : '--';
}

function formatCellValue(value: number | string | null | undefined) {
    if (typeof value === 'string') return value || '--';
    return formatPrice(value);
}

function normalizeDate(value: any) {
    return value ? String(value).slice(0, 10) : '';
}

function formatDateLabel(value: any) {
    const date = normalizeDate(value);
    return date ? date.slice(5) : '';
}

function getPaperLinePrice(paperLines: { price: number; label: string; color: string; date?: string }[] | undefined, label: string) {
    return validNumber((paperLines || []).find((line) => line.label.includes(label))?.price);
}

function getPaperLineDate(paperLines: { price: number; label: string; color: string; date?: string }[] | undefined, label: string) {
    return normalizeDate((paperLines || []).find((line) => line.label.includes(label))?.date);
}

function SummaryBox({
    title,
    body,
    tone,
    priceRows,
}: {
    title: string;
    body: string;
    tone: 'positive' | 'risk' | 'neutral';
    priceRows?: { label: string; value: number | string | null | undefined; color?: string }[];
}) {
    const Icon = tone === 'positive' ? CheckCircle2 : tone === 'risk' ? ShieldAlert : AlertTriangle;
    return (
        <div className={cn(
            "border-t px-4 py-3 flex items-start gap-3",
            tone === 'positive' && "bg-emerald-50/70 border-emerald-100 text-emerald-800",
            tone === 'risk' && "bg-rose-50/70 border-rose-100 text-rose-800",
            tone === 'neutral' && "bg-slate-50 border-slate-100 text-slate-700"
        )}>
            <Icon size={15} className="mt-0.5 shrink-0" />
            <div className="min-w-0 flex-1">
                <div className="text-xs font-black">{title}</div>
                <p className="mt-1 text-[11px] font-semibold leading-relaxed">{body}</p>
                {priceRows && priceRows.length > 0 && (
                    <div className="mt-3 overflow-x-auto rounded-md border border-white/70 bg-white/70">
                        <table className="w-full min-w-[720px] text-left">
                            <thead className="bg-slate-50/80">
                                <tr>
                                    {priceRows.map((row) => (
                                        <th key={row.label} className="px-3 py-2 text-[10px] font-black text-slate-400 tracking-widest">
                                            {row.label}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    {priceRows.map((row) => (
                                        <td key={row.label} className={cn("px-3 py-2 font-mono text-sm font-black text-slate-700", row.color)}>
                                            {formatCellValue(row.value)}
                                        </td>
                                    ))}
                                </tr>
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

export default function SplitKLineCharts({
    candles,
    emaLines = [],
    rfFilter,
    trailingStops,
    markers,
    buySignals,
    sellSignals,
    priceAction,
    priceActionLines,
    paperLines,
    riskLevels,
    height = 360,
    compact = false,
}: SplitKLineChartsProps) {
    const strategyRef = useRef<HTMLDivElement>(null);
    const priceActionRef = useRef<HTMLDivElement>(null);
    const strategyVisibleRangeRef = useRef<any>(null);
    const priceActionVisibleRangeRef = useRef<any>(null);

    const sortedCandles = useMemo(() => normalizeCandles(candles), [candles]);
    const buyDate = normalizeDate(riskLevels?.entry_date || getPaperLineDate(paperLines, '买入'));
    const signalMarkers = useMemo(() => {
        const all = [...(markers || []), ...buildSignalMarkers(buySignals, sellSignals)];
        if (buyDate && sortedCandles.some((c) => c.time === buyDate)) {
            all.push({
                time: buyDate,
                position: 'belowBar',
                color: '#4f46e5',
                shape: 'arrowUp',
                text: `买入 ${formatDateLabel(buyDate)}`,
            });
        }
        const seen = new Set<string>();
        return all
            .filter((marker) => marker?.time && !String(marker.text || '').startsWith('PA'))
            .sort((a, b) => String(a.time).localeCompare(String(b.time)))
            .filter((marker) => {
                const key = `${marker.time}-${marker.position}-${marker.text}`;
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            });
    }, [markers, buySignals, sellSignals, buyDate, sortedCandles]);
    const priceActionMarkers = useMemo(() => {
        return (markers || [])
            .filter((marker) => marker?.time && String(marker.text || '').startsWith('PA'))
            .sort((a, b) => String(a.time).localeCompare(String(b.time)));
    }, [markers]);

    const strategySummary = useMemo(
        () => {
            const base = buildStrategySummary(sortedCandles, trailingStops, buySignals, sellSignals);
            const notes = Array.isArray(riskLevels?.risk_notes) ? riskLevels.risk_notes.filter(Boolean) : [];
            return notes.length > 0 ? { ...base, body: `${base.body} ${notes.join('；')}。` } : base;
        },
        [sortedCandles, trailingStops, buySignals, sellSignals, riskLevels]
    );
    const paSummary = useMemo(
        () => buildPriceActionSummary(priceAction, priceActionLines),
        [priceAction, priceActionLines]
    );
    const priceRows = useMemo(() => {
        const latestTrailing = normalizeLine(trailingStops).at(-1)?.value;
        const movingRisk = riskLevels ? validNumber(riskLevels?.moving_stop_price) : latestTrailing;
        return [
            {
                label: '买入日期',
                value: buyDate || '--',
                color: 'text-slate-700',
            },
            {
                label: '买入价',
                value: validNumber(riskLevels?.buy_price) ?? getPaperLinePrice(paperLines, '买入') ?? validNumber(priceAction?.pa_entry_price),
                color: 'text-indigo-700',
            },
            {
                label: '初始止损',
                value: validNumber(riskLevels?.initial_stop_price),
                color: 'text-rose-700',
            },
            {
                label: '结构失效',
                value: validNumber(riskLevels?.structure_stop_price) ?? validNumber(priceAction?.pa_stop_price),
                color: 'text-blue-700',
            },
            {
                label: '移动风控',
                value: movingRisk,
                color: 'text-orange-700',
            },
            {
                label: '执行风控',
                value: validNumber(riskLevels?.stop_price) ?? getPaperLinePrice(paperLines, '止损') ?? validNumber(priceAction?.pa_stop_price),
                color: 'text-rose-700',
            },
            {
                label: '止盈价',
                value: validNumber(riskLevels?.take_profit_price) ?? getPaperLinePrice(paperLines, '止盈') ?? validNumber(priceAction?.pa_target_price),
                color: 'text-emerald-700',
            },
        ];
    }, [paperLines, priceAction, riskLevels, trailingStops, buyDate]);

    useEffect(() => {
        if (!strategyRef.current || !priceActionRef.current || sortedCandles.length === 0) return;

        const strategyChart = createChart(strategyRef.current, {
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(148, 163, 184, 0.08)' }, horzLines: { color: 'rgba(148, 163, 184, 0.08)' } },
            width: strategyRef.current.clientWidth,
            height,
            timeScale: { borderColor: 'rgba(148, 163, 184, 0.18)', timeVisible: !compact },
            rightPriceScale: { borderColor: 'rgba(148, 163, 184, 0.18)' },
        });

        const paChart = createChart(priceActionRef.current, {
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#64748b' },
            grid: { vertLines: { color: 'rgba(148, 163, 184, 0.08)' }, horzLines: { color: 'rgba(148, 163, 184, 0.08)' } },
            width: priceActionRef.current.clientWidth,
            height,
            timeScale: { borderColor: 'rgba(148, 163, 184, 0.18)', timeVisible: !compact },
            rightPriceScale: { borderColor: 'rgba(148, 163, 184, 0.18)' },
        });

        const strategyCandles = strategyChart.addSeries(CandlestickSeries, {
            upColor: '#ef4444',
            downColor: '#22c55e',
            borderVisible: false,
            wickUpColor: '#ef4444',
            wickDownColor: '#22c55e',
        });
        strategyCandles.setData(sortedCandles);

        const paCandles = paChart.addSeries(CandlestickSeries, {
            upColor: 'rgba(239, 68, 68, 0.42)',
            downColor: 'rgba(34, 197, 94, 0.42)',
            borderVisible: false,
            wickUpColor: 'rgba(239, 68, 68, 0.42)',
            wickDownColor: 'rgba(34, 197, 94, 0.42)',
        });
        paCandles.setData(sortedCandles);

        emaLines.forEach((line) => {
            const series = strategyChart.addSeries(LineSeries, {
                color: line.color,
                lineWidth: (line.width || 1) as any,
                title: line.label,
                lastValueVisible: false,
                priceLineVisible: false,
            });
            series.setData(normalizeLine(sortedCandles, line.key));
        });

        if (rfFilter?.length) {
            const series = strategyChart.addSeries(LineSeries, {
                color: '#f59e0b',
                lineWidth: 2,
                title: 'Range Filter',
                lastValueVisible: false,
                priceLineVisible: false,
            });
            series.setData(normalizeLine(rfFilter));
        }

        if (trailingStops?.length) {
            const series = strategyChart.addSeries(LineSeries, {
                color: '#f97316',
                lineWidth: 2,
                lineStyle: 2,
                title: '移动风控线',
                lastValueVisible: false,
                priceLineVisible: false,
            });
            series.setData(normalizeLine(trailingStops));
        }

        (paperLines || []).forEach((line) => {
            const price = validNumber(line.price);
            if (price == null || price <= 0) return;
            strategyCandles.createPriceLine({
                price,
                color: line.color,
                lineWidth: 2,
                lineStyle: 2,
                axisLabelVisible: true,
                title: `${line.label} ${price.toFixed(2)}${line.label.includes('买入') && (line.date || buyDate) ? ` ${formatDateLabel(line.date || buyDate)}` : ''}`,
            });
        });

        if (signalMarkers.length > 0) {
            const markerPlugin = createSeriesMarkers(strategyCandles);
            markerPlugin.setMarkers(signalMarkers);
        }
        if (priceActionMarkers.length > 0) {
            const paMarkerPlugin = createSeriesMarkers(paCandles);
            paMarkerPlugin.setMarkers(priceActionMarkers);
        }

        (priceActionLines || []).forEach((line) => {
            const points = (line.points || [])
                .map((point: any) => ({ time: String(point.time), value: validNumber(point.value) }))
                .filter((point: any) => point.time && point.value != null)
                .sort((a: any, b: any) => a.time.localeCompare(b.time));
            if (points.length < 2) return;
            const series = paChart.addSeries(LineSeries, {
                color: line.color || '#2563eb',
                lineWidth: line.kind === 'entry' || line.kind === 'stop' ? 1 : 2,
                lineStyle: line.style === 'dotted' ? 1 : line.style === 'dashed' ? 2 : 0,
                title: line.label,
                lastValueVisible: line.kind === 'entry' || line.kind === 'stop',
                priceLineVisible: false,
            });
            series.setData(points);
        });

        if (strategyVisibleRangeRef.current) {
            strategyChart.timeScale().setVisibleLogicalRange(strategyVisibleRangeRef.current);
        } else {
            strategyChart.timeScale().fitContent();
        }
        if (priceActionVisibleRangeRef.current) {
            paChart.timeScale().setVisibleLogicalRange(priceActionVisibleRangeRef.current);
        } else {
            paChart.timeScale().fitContent();
        }

        const handleStrategyRangeChange = (range: any) => {
            if (range) strategyVisibleRangeRef.current = range;
        };
        const handlePriceActionRangeChange = (range: any) => {
            if (range) priceActionVisibleRangeRef.current = range;
        };
        strategyChart.timeScale().subscribeVisibleLogicalRangeChange(handleStrategyRangeChange);
        paChart.timeScale().subscribeVisibleLogicalRangeChange(handlePriceActionRangeChange);

        const handleResize = () => {
            if (strategyRef.current) strategyChart.applyOptions({ width: strategyRef.current.clientWidth });
            if (priceActionRef.current) paChart.applyOptions({ width: priceActionRef.current.clientWidth });
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            strategyChart.timeScale().unsubscribeVisibleLogicalRangeChange(handleStrategyRangeChange);
            paChart.timeScale().unsubscribeVisibleLogicalRangeChange(handlePriceActionRangeChange);
            strategyChart.remove();
            paChart.remove();
        };
    }, [sortedCandles, emaLines, rfFilter, trailingStops, signalMarkers, priceActionMarkers, priceActionLines, paperLines, height, compact]);

    return (
        <div className="grid grid-cols-1 divide-y divide-slate-200">
            <div className="min-w-0">
                <div className="px-4 py-2 border-b border-slate-100 flex items-center justify-between gap-3">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">K线与策略信号</span>
                    <span className="text-[10px] font-bold text-orange-600 flex items-center gap-1">
                        <TrendingUp size={12} /> 趋势 / 均线 / 风控
                    </span>
                </div>
                <div ref={strategyRef} className="w-full" style={{ height }} />
                <SummaryBox {...strategySummary} priceRows={priceRows} />
            </div>
            <div className="min-w-0">
                <div className="px-4 py-2 border-b border-slate-100 flex items-center justify-between gap-3">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">价格行为结构</span>
                    <span className="text-[10px] font-bold text-blue-600">趋势线 / 入场 / 失效</span>
                </div>
                <div ref={priceActionRef} className="w-full" style={{ height }} />
                <SummaryBox {...paSummary} />
            </div>
        </div>
    );
}
