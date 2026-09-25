"use client";

import React, { useEffect, useMemo, useRef, useState } from 'react';
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
import { selectTvStrictSignals, selectWaveDisplaySignals } from '@/lib/signalDisplay';
import { buildRiskPriceLines, getEffectiveStopPrice } from '@/lib/tradingLevels';
import { buildDowPhaseMarkers, buildProjectionScenarioMarkers, futureWhitespaceCandles, hintLevelStyle, projectionPriceLines, type ChartHint, type DowPhasePoint, type TradeProjection } from '@/lib/dowPhase';

export interface ChipDistributionBar {
    price: number;
    weight: number;
    profitable: boolean;
}

export interface ChipDistribution {
    available: boolean;
    as_of?: string;
    peak_price?: number;
    average_cost?: number;
    profit_ratio?: number;
    cost_70_low?: number;
    cost_70_high?: number;
    migration?: string;
    buy_impact?: string;
    holding_impact?: string;
    score_delta?: number;
    bars?: ChipDistributionBar[];
}

interface SplitKLineChartsProps {
    candles: any[];
    emaLines?: { key: string; label: string; color: string; width?: number }[];
    rfFilter?: any[];
    trailingStops?: any[];
    markers?: any[];
    buySignals?: any[];
    sellSignals?: any[];
    strategySignalSets?: Record<string, any>;
    priceAction?: any;
    priceActionLines?: any[];
    trendPhases?: DowPhasePoint[];
    chartHints?: ChartHint[];
    tradeProjection?: TradeProjection | null;
    paperLines?: { price: number; label: string; color: string; date?: string }[];
    riskLevels?: any;
    chipDistribution?: ChipDistribution | null;
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

function buildSignalMarkers(buySignals?: any[], sellSignals?: any[], source?: string) {
    const markers: any[] = [];
    const displayBuys = source === 'squeeze'
        ? selectWaveDisplaySignals(buySignals, sellSignals)
        : (buySignals || []).map((signal) => ({ signal, role: 'entry' as const }));
    displayBuys.forEach(({ signal: sig, role }) => {
        if (!sig?.time) return;
        const reason = String(sig.reason || '');
        const isTv = source === 'tv_zp' || reason.includes('TV-ZP');
        markers.push({
            time: String(sig.time),
            position: 'belowBar',
            color: isTv ? '#22c55e' : '#ef4444',
            shape: 'arrowUp',
            text: isTv ? 'long' : (role === 'confirmation' ? '均线确认' : '均线买点'),
            source: source || (isTv ? 'tv_zp' : 'active'),
        });
    });
    (sellSignals || []).forEach((sig) => {
        if (!sig?.time) return;
        const reason = String(sig.reason || '');
        const isTv = source === 'tv_zp' || reason.includes('TV-ZP');
        const text = isTv
            ? 'short'
            : reason.includes('止盈')
            ? reason
            : reason.includes('止损')
                ? '回测止损'
                : '破位';
        markers.push({
            time: String(sig.time),
            position: 'aboveBar',
            color: isTv ? '#ef4444' : (reason.includes('止损') ? '#f59e0b' : '#a855f7'),
            shape: 'arrowDown',
            text,
            source: source || (isTv ? 'tv_zp' : 'active'),
        });
    });
    return markers.sort((a, b) => a.time.localeCompare(b.time));
}

function getSignalMarkerTone(marker: any) {
    const text = String(marker?.text || '');
    if (marker?.source === 'mtr_pullback_rebreak') return { badge: '回踩', label: '企稳突破·观察', color: '#0f766e' };
    if (marker?.source === 'tv_strict') return marker.sameDay
        ? { badge: 'TV', label: '同日强共振·观察', color: '#7c3aed' }
        : { badge: 'TV', label: '3日双命中·待确认', color: '#d97706' };
    if (text.includes('Bark')) return { badge: 'Bark', label: '推荐日', color: '#0ea5e9' };
    if (text.toLowerCase().includes('long')) return { badge: 'B', label: 'long', color: '#22c55e' };
    if (text.toLowerCase().includes('short')) return { badge: 'S', label: 'short', color: '#ef4444' };
    if (text.includes('均线确认')) return { badge: '均线', label: '确认', color: '#f97316' };
    if (text.includes('均线买点')) return { badge: '均线', label: '买点', color: '#ef4444' };
    if (text.includes('共振')) return { badge: '均线', label: '买点', color: '#ef4444' };
    if (text.includes('破位')) return { badge: '破', label: '破位', color: '#a855f7' };
    if (text.includes('买入')) return { badge: 'B', label: '买入', color: '#4f46e5' };
    if (text.includes('买')) return { badge: 'B', label: '买点', color: '#ef4444' };
    if (text.includes('止损')) return { badge: '止', label: '止损', color: '#f59e0b' };
    if (text.includes('止盈')) return { badge: '↓', label: text, color: '#a855f7' };
    if (text.includes('卖')) return { badge: 'S', label: '卖点', color: '#16a34a' };
    return { badge: '•', label: text || '信号', color: marker?.color || '#64748b' };
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
            title: '策略风控线偏近',
            body: `现价距离策略风控线约 ${stopGap.toFixed(1)}%，这是策略图生成的参考线，不等同于持仓表里的移动风控。后续以执行风控价和结构失效位为主。`,
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
    const volumePullbackStatus = String(priceAction?.pa_volume_pullback_status || 'NONE');
    const volumePullbackLabel = String(priceAction?.pa_volume_pullback_label || '');
    const volumePullbackConfirmation = String(priceAction?.pa_volume_pullback_confirmation_label || '');
    const structureNote = priceAction?.pa_structure_state_label
        ? `当前结构：${priceAction.pa_structure_state_label}；${priceAction.pa_structure_state_action || ''}。`
        : '';
    const intradayNote = priceAction?.pa_mtf_intraday?.label
        ? `多周期：${priceAction.pa_mtf_intraday.label}。`
        : '';
    const volumePullbackNote = volumePullbackStatus !== 'NONE' && volumePullbackLabel
        ? `当前突破回踩状态：${volumePullbackLabel}${volumePullbackConfirmation ? `（${volumePullbackConfirmation}）` : ''}。`
        : '';

    if (summary) {
        const action = score != null && score >= 70
            ? '结构评分较高，若右图入场线被放量突破，可小仓试错并严格按失效位止损。'
            : score != null && score < 55
                ? '结构优势不足，优先等待更明确的二次突破或回踩确认。'
                : '结构处在可观察区间，适合等待价格靠近入场线或失效线后再做决策。';
        return {
            title: `${regime || '价格行为'}${quality ? ` · ${quality}` : ''}`,
            body: `${summary} ${volumePullbackNote} ${structureNote} ${intradayNote} ${action}`,
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

function formatPct(value: number | null | undefined) {
    return value != null && Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '--';
}

function getPaperLinePrice(paperLines: { price: number; label: string; color: string; date?: string }[] | undefined, label: string) {
    return validNumber((paperLines || []).find((line) => line.label.includes(label))?.price);
}

function getPaperLineDate(paperLines: { price: number; label: string; color: string; date?: string }[] | undefined, label: string) {
    return normalizeDate((paperLines || []).find((line) => line.label.includes(label))?.date);
}

function sectorPhaseTone(phase?: string) {
    const map: Record<string, { label: string; bg: string; text: string }> = {
        SECTOR_CONFIRM: { label: '板块确认', bg: 'rgba(16, 185, 129, 0.055)', text: 'text-emerald-700' },
        SECTOR_WARMUP: { label: '板块预热', bg: 'rgba(245, 158, 11, 0.06)', text: 'text-amber-700' },
        SECTOR_FADE: { label: '板块退潮', bg: 'rgba(244, 63, 94, 0.055)', text: 'text-rose-700' },
        SECTOR_NEUTRAL: { label: '板块中性', bg: 'rgba(100, 116, 139, 0.04)', text: 'text-slate-500' },
    };
    return map[phase || ''] || map.SECTOR_NEUTRAL;
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
    strategySignalSets,
    priceAction,
    priceActionLines,
    trendPhases,
    chartHints,
    tradeProjection,
    paperLines,
    riskLevels,
    chipDistribution,
    height = 360,
    compact = false,
}: SplitKLineChartsProps) {
    const strategyRef = useRef<HTMLDivElement>(null);
    const priceActionRef = useRef<HTMLDivElement>(null);
    const strategyOverlayRef = useRef<HTMLDivElement>(null);
    const priceActionOverlayRef = useRef<HTMLDivElement>(null);
    const strategyTooltipRef = useRef<HTMLDivElement>(null);
    const priceActionTooltipRef = useRef<HTMLDivElement>(null);
    const chipOverlayRef = useRef<HTMLDivElement>(null);
    const strategyVisibleRangeRef = useRef<any>(null);
    const priceActionVisibleRangeRef = useRef<any>(null);
    const [showChips, setShowChips] = useState(true);

    const sortedCandles = useMemo(() => normalizeCandles(candles), [candles]);
    const buyDate = normalizeDate(riskLevels?.entry_date || getPaperLineDate(paperLines, '买入'));
    const barkDate = normalizeDate(riskLevels?.bark_recommendation_date || riskLevels?.latest_scan_date || riskLevels?.entry_signal_date);
    const riskPriceLines = useMemo(() => buildRiskPriceLines(riskLevels, paperLines), [paperLines, riskLevels]);
    const sectorTone = useMemo(() => sectorPhaseTone(riskLevels?.sector_phase), [riskLevels]);
    const chipProfile = useMemo(() => {
        if (!chipDistribution?.available || !Array.isArray(chipDistribution?.bars)) return null;
        const bars = chipDistribution.bars
            .map((bar: ChipDistributionBar) => ({
                price: validNumber(bar?.price),
                weight: validNumber(bar?.weight),
                profitable: Boolean(bar?.profitable),
            }))
            .filter((bar): bar is { price: number; weight: number; profitable: boolean } => (
                bar.price != null && bar.weight != null && bar.weight > 0
            ))
            .sort((a, b) => a.price - b.price);
        const maxWeight = Math.max(0, ...bars.map((bar) => bar.weight));
        return bars.length > 0 && maxWeight > 0 ? { bars, maxWeight } : null;
    }, [chipDistribution]);
    const tvStrictSignals = useMemo(() => selectTvStrictSignals(
        sortedCandles.map((candle) => candle.time),
        strategySignalSets?.squeeze?.buy_signals,
        strategySignalSets?.tv_zp?.buy_signals,
    ), [sortedCandles, strategySignalSets]);
    const latestTvStrictSignal = tvStrictSignals.at(-1);
    const recentTvDates = sortedCandles.slice(-3).map((candle) => candle.time);
    const currentTvStrictSignal = latestTvStrictSignal
        && recentTvDates.some((date) => date === latestTvStrictSignal.time)
        && recentTvDates.some((date) => (strategySignalSets?.squeeze?.buy_signals || []).some((signal: { time?: string }) => signal.time === date))
        && recentTvDates.some((date) => (strategySignalSets?.tv_zp?.buy_signals || []).some((signal: { time?: string }) => signal.time === date))
        && !(strategySignalSets?.tv_zp?.sell_signals || []).some((signal: { time?: string }) => (
            signal.time && signal.time > latestTvStrictSignal.time
        ))
        ? latestTvStrictSignal
        : null;
    const currentPullbackRebreak = (markers || []).find((marker) => (
        marker?.source === 'mtr_pullback_rebreak'
        && marker?.time === sortedCandles.at(-1)?.time
    ));
    const signalMarkers = useMemo(() => {
        const overlayMarkers = Object.entries(strategySignalSets || {}).flatMap(([strategy, set]) => (
            buildSignalMarkers(set?.buy_signals || [], set?.sell_signals || [], strategy)
        ));
        const overlaySignalKeys = new Set(
            Object.values(strategySignalSets || {}).flatMap((set) => [
                ...(set?.buy_signals || []).map((signal: { time?: unknown }) => `${signal?.time}-belowBar`),
                ...(set?.sell_signals || []).map((signal: { time?: unknown }) => `${signal?.time}-aboveBar`),
            ]),
        );
        const all = [
            ...(markers || []).filter((marker) => (
                marker?.source === 'mtr_pullback_rebreak'
                || !overlaySignalKeys.has(`${marker?.time}-${marker?.position}`)
            )),
            ...buildDowPhaseMarkers(trendPhases),
            ...buildProjectionScenarioMarkers(tradeProjection, String(sortedCandles[sortedCandles.length - 1]?.time || '')),
            ...overlayMarkers,
            ...tvStrictSignals.map((signal) => ({
                time: signal.time,
                position: 'aboveBar',
                color: signal.sameDay ? '#7c3aed' : '#d97706',
                shape: 'circle',
                text: signal.sameDay ? '同日强共振·观察' : '3日双命中·待确认',
                source: 'tv_strict',
                sameDay: signal.sameDay,
            })),
            ...(overlayMarkers.length === 0 ? buildSignalMarkers(buySignals, sellSignals) : []),
        ];
        if (buyDate && sortedCandles.some((c) => c.time === buyDate)) {
            all.push({
                time: buyDate,
                position: 'belowBar',
                color: '#4f46e5',
                shape: 'arrowUp',
                text: `买入 ${formatDateLabel(buyDate)}`,
                source: 'paper',
            });
        }
        if (barkDate && sortedCandles.some((c) => c.time === barkDate)) {
            all.push({
                time: barkDate,
                position: 'belowBar',
                color: '#0ea5e9',
                shape: 'circle',
                text: `Bark ${formatDateLabel(barkDate)}`,
                source: 'bark',
            });
        }
        const seen = new Set<string>();
        return all
            .filter((marker) => (
                marker?.time
                && marker?.source !== 'eight_rule'
                && marker?.source !== 'price_action_second_entry'
                && !String(marker.text || marker.label || '').includes('冲高回落')
                && !String(marker.text || '').startsWith('PA')
            ))
            .sort((a, b) => String(a.time).localeCompare(String(b.time)))
            .filter((marker) => {
                const key = `${marker.time}-${marker.position}-${marker.text}`;
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            });
    }, [markers, buySignals, sellSignals, strategySignalSets, tvStrictSignals, buyDate, barkDate, sortedCandles, trendPhases, tradeProjection]);
    const priceActionMarkers = useMemo(() => {
        return (markers || [])
            .filter((marker) => (
                marker?.time
                && marker?.source !== 'eight_rule'
                && !String(marker.text || marker.label || '').includes('冲高回落')
                && (
                    marker?.source === 'price_action_second_entry'
                    || String(marker.text || '').startsWith('PA')
                )
            ))
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
        const movingRisk = validNumber(riskLevels?.moving_stop_price);
        const effectiveStop = getEffectiveStopPrice(riskLevels);
        return [
            {
                label: '买入日期',
                value: buyDate || '--',
                color: 'text-slate-700',
            },
            {
                label: '持仓成本',
                value: validNumber(riskLevels?.buy_price) ?? getPaperLinePrice(paperLines, '买入') ?? validNumber(priceAction?.pa_entry_price),
                color: 'text-indigo-700',
            },
            {
                label: '弱市止损参考',
                value: validNumber(riskLevels?.initial_stop_price),
                color: 'text-rose-700',
            },
            {
                label: movingRisk != null ? '移动风控' : '策略回测风控',
                value: movingRisk ?? latestTrailing,
                color: 'text-orange-700',
            },
            {
                label: validNumber(riskLevels?.structure_stop_price) != null ? '执行风控/结构失效' : '执行风控',
                value: effectiveStop ?? getPaperLinePrice(paperLines, '止损') ?? validNumber(priceAction?.pa_stop_price),
                color: 'text-rose-700',
            },
            {
                label: '第一止盈目标',
                value: validNumber(riskLevels?.take_profit_price) ?? getPaperLinePrice(paperLines, '止盈') ?? validNumber(priceAction?.pa_target_price),
                color: 'text-emerald-700',
            },
        ];
    }, [paperLines, priceAction, riskLevels, trailingStops, buyDate]);
    const overlayCounts = useMemo(() => {
        const maSignals = strategySignalSets?.squeeze;
        const tvSignals = strategySignalSets?.tv_zp;
        return [
            {
                label: '均线策略',
                value: `${maSignals?.buy_count ?? maSignals?.buy_signals?.length ?? 0}买 / ${maSignals?.sell_count ?? maSignals?.sell_signals?.length ?? 0}卖`,
                color: 'text-rose-600 bg-rose-50 border-rose-100',
            },
            {
                label: 'TV策略',
                value: `${tvSignals?.buy_count ?? tvSignals?.buy_signals?.length ?? 0} long / ${tvSignals?.sell_count ?? tvSignals?.sell_signals?.length ?? 0} short`,
                color: 'text-emerald-700 bg-emerald-50 border-emerald-100',
            },
        ];
    }, [strategySignalSets]);

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
        // 未来空白K线（仅 time）：为"执行策略投影"的箭头标记（回踩买点/目标位/失效离场）
        // 留出可标注的未来区域；历史K线部分不受影响。
        const lastChartCandle = sortedCandles[sortedCandles.length - 1];
        const futureBars = futureWhitespaceCandles(String(lastChartCandle?.time || ''), 14);
        strategyCandles.setData([...sortedCandles, ...futureBars]);

        const paCandles = paChart.addSeries(CandlestickSeries, {
            upColor: 'rgba(239, 68, 68, 0.42)',
            downColor: 'rgba(34, 197, 94, 0.42)',
            borderVisible: false,
            wickUpColor: 'rgba(239, 68, 68, 0.42)',
            wickDownColor: 'rgba(34, 197, 94, 0.42)',
        });
        paCandles.setData([...sortedCandles, ...futureBars]);

        if (showChips && chipProfile) {
            [
                { price: validNumber(chipDistribution?.peak_price), color: '#7c3aed', title: '筹码峰' },
                { price: validNumber(chipDistribution?.average_cost), color: '#f59e0b', title: '筹码均价' },
                { price: validNumber(chipDistribution?.cost_70_low), color: 'rgba(13, 148, 136, 0.65)', title: '70%成本下沿' },
                { price: validNumber(chipDistribution?.cost_70_high), color: 'rgba(13, 148, 136, 0.65)', title: '70%成本上沿' },
            ].forEach((line) => {
                if (line.price == null || line.price <= 0) return;
                paCandles.createPriceLine({
                    price: line.price,
                    color: line.color,
                    lineWidth: line.title === '筹码峰' ? 2 : 1,
                    lineStyle: 2,
                    axisLabelVisible: line.title === '筹码峰' || line.title === '筹码均价',
                    title: `${line.title} ${line.price.toFixed(2)}`,
                });
            });
        }

        emaLines.forEach((line) => {
            const series = strategyChart.addSeries(LineSeries, {
                color: line.color,
                lineWidth: (line.width || 1) as any,
                title: line.label,
                lastValueVisible: true,
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
                color: '#94a3b8',
                lineWidth: 1,
                lineStyle: 2,
                title: '策略回测风控（非持仓）',
                lastValueVisible: true,
                priceLineVisible: false,
            });
            series.setData(normalizeLine(trailingStops));
        }

        riskPriceLines.forEach((line) => {
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

        // 执行策略投影：关键价位右轴点线（未来走势改用箭头标记，见 signalMarkers）。
        // 数据来自后端 build_trade_projection（PA 关键位的规则推演，非预测）。
        projectionPriceLines(tradeProjection, riskPriceLines.map((line) => validNumber(line.price))).forEach((level) => {
            strategyCandles.createPriceLine({
                price: level.price,
                color: level.label.includes('失效') || level.label.includes('止损') ? '#dc2626'
                    : level.label.includes('目标') ? '#16a34a' : '#0d9488',
                lineWidth: 1,
                lineStyle: 3,
                axisLabelVisible: true,
                title: `${level.label} ${level.price.toFixed(2)}`,
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

        const candleByTime = new Map(sortedCandles.map((c, index) => [String(c.time), { candle: c, index }]));
        const renderHoverTooltip = (tooltip: HTMLDivElement | null, param: any, chart: IChartApi) => {
            if (!tooltip) return;
            if (!param?.time || !param?.point) {
                tooltip.style.display = 'none';
                return;
            }
            const match = candleByTime.get(String(param.time));
            if (!match) {
                tooltip.style.display = 'none';
                return;
            }
            const { candle, index } = match;
            const prev = index > 0 ? sortedCandles[index - 1] : null;
            const base = prev?.close && prev.close > 0 ? prev.close : candle.open;
            const dayPct = base > 0 ? ((candle.close - base) / base) * 100 : null;
            const isUp = (dayPct || 0) >= 0;
            tooltip.innerHTML = `
                <div class="text-[10px] font-black text-slate-400">${String(candle.time)}</div>
                <div class="mt-1 flex items-center gap-3">
                    <span class="font-black text-slate-700">最新价 ${formatPrice(candle.close)}</span>
                    <span class="font-black ${isUp ? 'text-rose-600' : 'text-emerald-600'}">涨幅 ${formatPct(dayPct)}</span>
                </div>
            `;
            const containerWidth = chart.options().width || 0;
            const left = Math.min(Math.max(param.point.x + 12, 8), Math.max(8, Number(containerWidth) - 170));
            const top = Math.max(8, Math.min(height - 56, param.point.y + 12));
            tooltip.style.left = `${left}px`;
            tooltip.style.top = `${top}px`;
            tooltip.style.display = 'block';
        };

        (priceActionLines || []).forEach((line) => {
            // 同一条结构线两端点可能落在同一天（如突破日即当天），须按时间去重，
            // 否则 lightweight-charts 的 setData 严格升序断言会直接崩溃。
            const pointMap = new Map<string, number>();
            (line.points || []).forEach((point: any) => {
                if (!point?.time) return;
                const value = validNumber(point.value);
                if (value == null) return;
                pointMap.set(String(point.time), value);
            });
            const points = Array.from(pointMap.entries())
                .map(([time, value]) => ({ time, value }))
                .sort((a, b) => a.time.localeCompare(b.time));
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

        const renderTradeLabels = () => {
            const overlay = strategyOverlayRef.current;
            if (!overlay) return;
            overlay.innerHTML = '';
            const barkX = barkDate ? strategyChart.timeScale().timeToCoordinate(barkDate as any) : null;
            if (barkX != null) {
                const line = document.createElement('div');
                line.className = 'absolute top-0 bottom-0 z-10 border-l border-dashed border-sky-500/70';
                line.style.left = `${barkX}px`;
                line.style.pointerEvents = 'none';
                const tag = document.createElement('div');
                tag.className = 'absolute top-2 -translate-x-1/2 rounded bg-sky-500 px-2 py-1 text-[10px] font-black text-white shadow';
                tag.style.left = `${barkX}px`;
                tag.textContent = 'Bark推荐日';
                overlay.appendChild(line);
                overlay.appendChild(tag);
            }
            const candleByTime = new Map(sortedCandles.map((c) => [String(c.time), c]));
            signalMarkers.forEach((marker) => {
                const candle = candleByTime.get(String(marker.time));
                if (!candle) return;
                const x = strategyChart.timeScale().timeToCoordinate(String(marker.time) as any);
                const anchorPrice = marker.position === 'aboveBar' ? candle.high : candle.low;
                const y = strategyCandles.priceToCoordinate(anchorPrice);
                if (x == null || y == null) return;

                const tone = getSignalMarkerTone(marker);
                const isAbove = marker.position === 'aboveBar';
                const label = document.createElement('div');
                label.className = 'absolute z-20 flex flex-col items-center select-none';
                label.style.left = `${x}px`;
                label.style.top = `${Math.max(4, Math.min(height - 48, y + (isAbove ? -44 : 12)))}px`;
                label.style.transform = 'translateX(-50%)';
                label.style.pointerEvents = 'none';

                const box = document.createElement('div');
                box.className = 'min-w-[34px] rounded-md px-2 py-1 text-center text-[10px] font-black leading-tight text-white shadow-lg ring-1 ring-white/60';
                box.style.background = tone.color;
                box.innerHTML = `<div class="text-[11px]">${tone.badge}</div><div>${tone.label}</div>`;

                const arrow = document.createElement('div');
                arrow.style.width = '0';
                arrow.style.height = '0';
                arrow.style.borderLeft = '5px solid transparent';
                arrow.style.borderRight = '5px solid transparent';
                if (isAbove) {
                    arrow.style.borderTop = `6px solid ${tone.color}`;
                    label.appendChild(box);
                    label.appendChild(arrow);
                } else {
                    arrow.style.borderBottom = `6px solid ${tone.color}`;
                    label.appendChild(arrow);
                    label.appendChild(box);
                }
                overlay.appendChild(label);
            });
        };

        const renderPriceActionLabels = () => {
            const overlay = priceActionOverlayRef.current;
            if (!overlay) return;
            overlay.innerHTML = '';
            const candleByTime = new Map(sortedCandles.map((c) => [String(c.time), c]));
            priceActionMarkers.forEach((marker) => {
                const candle = candleByTime.get(String(marker.time));
                if (!candle) return;
                const x = paChart.timeScale().timeToCoordinate(String(marker.time) as any);
                const anchorPrice = marker.position === 'aboveBar' ? candle.high : candle.low;
                const y = paCandles.priceToCoordinate(anchorPrice);
                if (x == null || y == null) return;
                const label = document.createElement('div');
                label.className = 'absolute z-20 rounded-md px-2 py-1 text-[10px] font-black text-white shadow-lg ring-1 ring-white/60 select-none';
                label.style.left = `${x}px`;
                label.style.top = `${Math.max(4, Math.min(height - 30, y + (marker.position === 'aboveBar' ? -34 : 10)))}px`;
                label.style.transform = 'translateX(-50%)';
                label.style.pointerEvents = 'none';
                label.style.background = marker.color || '#2563eb';
                label.textContent = String(marker.text || 'PA');
                overlay.appendChild(label);
            });
        };

        const renderChipProfile = () => {
            const overlay = chipOverlayRef.current;
            if (!overlay) return;
            overlay.innerHTML = '';
            if (!showChips || !chipProfile) return;
            chipProfile.bars.forEach((bar) => {
                const y = paCandles.priceToCoordinate(bar.price);
                if (y == null || y < 0 || y > height) return;
                const width = Math.max(2, (bar.weight / chipProfile.maxWeight) * 104);
                const item = document.createElement('div');
                item.className = 'absolute h-[3px] rounded-l-sm opacity-65';
                item.style.right = '58px';
                item.style.top = `${y - 1.5}px`;
                item.style.width = `${width}px`;
                item.style.background = bar.profitable ? '#f43f5e' : '#6366f1';
                overlay.appendChild(item);
            });
        };

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

        renderTradeLabels();
        renderPriceActionLabels();
        renderChipProfile();

        const handleStrategyRangeChange = (range: any) => {
            if (range) strategyVisibleRangeRef.current = range;
            renderTradeLabels();
        };
        const handlePriceActionRangeChange = (range: any) => {
            if (range) priceActionVisibleRangeRef.current = range;
            renderPriceActionLabels();
            renderChipProfile();
        };
        strategyChart.timeScale().subscribeVisibleLogicalRangeChange(handleStrategyRangeChange);
        paChart.timeScale().subscribeVisibleLogicalRangeChange(handlePriceActionRangeChange);

        const handleStrategyCrosshairMove = (param: any) => renderHoverTooltip(strategyTooltipRef.current, param, strategyChart);
        const handlePriceActionCrosshairMove = (param: any) => renderHoverTooltip(priceActionTooltipRef.current, param, paChart);
        strategyChart.subscribeCrosshairMove(handleStrategyCrosshairMove);
        paChart.subscribeCrosshairMove(handlePriceActionCrosshairMove);

        const handleResize = () => {
            if (strategyRef.current) strategyChart.applyOptions({ width: strategyRef.current.clientWidth });
            if (priceActionRef.current) paChart.applyOptions({ width: priceActionRef.current.clientWidth });
            renderTradeLabels();
            renderPriceActionLabels();
            renderChipProfile();
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            strategyChart.timeScale().unsubscribeVisibleLogicalRangeChange(handleStrategyRangeChange);
            paChart.timeScale().unsubscribeVisibleLogicalRangeChange(handlePriceActionRangeChange);
            strategyChart.unsubscribeCrosshairMove(handleStrategyCrosshairMove);
            paChart.unsubscribeCrosshairMove(handlePriceActionCrosshairMove);
            strategyChart.remove();
            paChart.remove();
        };
    }, [sortedCandles, emaLines, rfFilter, trailingStops, signalMarkers, priceActionMarkers, priceActionLines, riskPriceLines, barkDate, height, compact, chipDistribution, chipProfile, showChips]);

    return (
        <div className="grid grid-cols-1 divide-y divide-slate-200">
            <div className="min-w-0">
                <div className="px-4 py-2 border-b border-slate-100 flex items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">K线与策略信号</span>
                        {overlayCounts.map((item) => (
                            <span key={item.label} className={cn("rounded-md border px-2 py-1 text-[10px] font-black", item.color)}>
                                {item.label} {item.value}
                            </span>
                        ))}
                    </div>
                    <span className="text-[10px] font-bold text-orange-600 flex items-center gap-1">
                        <TrendingUp size={12} /> 均线共振 + TV long/short
                    </span>
                </div>
                {currentTvStrictSignal && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-100 bg-amber-50/70 px-4 py-1.5 text-[10px] font-bold text-slate-700">
                        <span className={currentTvStrictSignal.sameDay ? 'text-violet-700' : 'text-amber-700'}>
                            TV严格双信号：{currentTvStrictSignal.sameDay ? '同日强共振·观察' : '3日窗口双命中·待确认'}
                        </span>
                        {Number(priceAction?.pa_entry_price) > 0 && <span>价格行为触发参考 {Number(priceAction.pa_entry_price).toFixed(2)}</span>}
                        {Number(priceAction?.pa_stop_price) > 0 && <span>结构失效参考 {Number(priceAction.pa_stop_price).toFixed(2)}</span>}
                        <span>仅供研究观察，仍须按现有风控确认；不自动买入</span>
                    </div>
                )}
                {currentPullbackRebreak && (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-teal-100 bg-teal-50/70 px-4 py-1.5 text-[10px] font-bold text-teal-800">
                        <span>向上反转后首次回踩企稳突破·观察</span>
                        {Number(currentPullbackRebreak.trigger_price) > 0 && <span>突破参考 {Number(currentPullbackRebreak.trigger_price).toFixed(2)}</span>}
                        {Number(currentPullbackRebreak.pullback_low) > 0 && <span>回踩低点 {Number(currentPullbackRebreak.pullback_low).toFixed(2)}</span>}
                        <span>仅作结构提示，仍须按现有风控确认；不自动买入</span>
                    </div>
                )}
                {chartHints && chartHints.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 bg-slate-50/70 px-4 py-1.5">
                        {chartHints.map((hint, idx) => (
                            <span
                                key={idx}
                                className={cn("rounded border px-1.5 py-0.5 text-[10px] font-bold", hintLevelStyle(hint?.level))}
                            >
                                {hint?.text}
                            </span>
                        ))}
                    </div>
                )}
                <div className="relative w-full" style={{ height, background: sectorTone.bg }}>
                    {riskLevels?.sector_phase && (
                        <div className={cn("absolute left-3 top-3 z-10 rounded-md border border-white/70 bg-white/80 px-2 py-1 text-[10px] font-black shadow-sm", sectorTone.text)}>
                            {sectorTone.label}
                            {riskLevels?.sector_momentum_score != null ? ` · 强度${Number(riskLevels.sector_momentum_score).toFixed(0)}` : ''}
                        </div>
                    )}
                    <div ref={strategyRef} className="absolute inset-0" />
                    <div ref={strategyOverlayRef} className="absolute inset-0 z-10 pointer-events-none" />
                    <div
                        ref={strategyTooltipRef}
                        className="pointer-events-none absolute z-30 hidden rounded-md border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-lg ring-1 ring-slate-900/5"
                    />
                </div>
                <SummaryBox {...strategySummary} priceRows={priceRows} />
            </div>
            <div className="min-w-0">
                <div className="px-4 py-2 border-b border-slate-100 flex items-center justify-between gap-3">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">价格行为结构</span>
                    <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold text-blue-600">趋势线 / 回踩支撑 / 入场 / 失效</span>
                        {chipProfile ? (
                            <button
                                type="button"
                                onClick={() => setShowChips((visible) => !visible)}
                                className={cn(
                                    "rounded-md border px-2 py-1 text-[10px] font-black transition-colors",
                                    showChips
                                        ? "border-violet-200 bg-violet-50 text-violet-700"
                                        : "border-slate-200 bg-white text-slate-400"
                                )}
                            >
                                筹码峰 {showChips ? '开' : '关'}
                            </button>
                        ) : (
                            <span className="text-[10px] font-bold text-slate-400">筹码数据不足</span>
                        )}
                    </div>
                </div>
                <div className="relative w-full" style={{ height }}>
                    <div ref={priceActionRef} className="absolute inset-0" />
                    <div ref={chipOverlayRef} className="absolute inset-0 z-[5] overflow-hidden pointer-events-none" />
                    <div ref={priceActionOverlayRef} className="absolute inset-0 z-10 pointer-events-none" />
                    <div
                        ref={priceActionTooltipRef}
                        className="pointer-events-none absolute z-30 hidden rounded-md border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-lg ring-1 ring-slate-900/5"
                    />
                </div>
                {chipDistribution?.available && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-violet-100 bg-violet-50/50 px-4 py-2 text-[10px] font-bold text-slate-600">
                        <span className="font-black text-violet-700">估算筹码成本</span>
                        <span>峰值 {Number(chipDistribution.peak_price).toFixed(2)}</span>
                        <span>均价 {Number(chipDistribution.average_cost).toFixed(2)}</span>
                        <span>获利盘 {(Number(chipDistribution.profit_ratio) * 100).toFixed(1)}%</span>
                        <span>70%区间 {Number(chipDistribution.cost_70_low).toFixed(2)}–{Number(chipDistribution.cost_70_high).toFixed(2)}</span>
                        <span className={chipDistribution.migration === '筹码上移' ? 'text-rose-600' : chipDistribution.migration === '筹码下移' ? 'text-indigo-600' : 'text-slate-500'}>
                            {chipDistribution.migration}
                        </span>
                        <span className={chipDistribution.buy_impact === '抑制买入' ? 'text-red-600' : 'text-emerald-600'}>
                            买点：{chipDistribution.buy_impact}
                        </span>
                        <span>持仓：{chipDistribution.holding_impact}</span>
                        <span>评分 {Number(chipDistribution.score_delta || 0) > 0 ? '+' : ''}{chipDistribution.score_delta || 0}</span>
                        <span className="text-slate-400">截至 {chipDistribution.as_of} · 换手衰减估算，非主力成本</span>
                    </div>
                )}
                <SummaryBox {...paSummary} />
            </div>
        </div>
    );
}
