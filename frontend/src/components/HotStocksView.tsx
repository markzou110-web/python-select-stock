"use client";

import React from 'react';
import { ArrowUpRight, Flame, Loader2, RefreshCw, Signal, TrendingUp } from 'lucide-react';
import HotStockChart, { type HotStockChartPoint } from '@/components/HotStockChart';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

type RankPeriod = 'hour' | 'day';
type ChartPeriod = 'minute' | 'day';

interface HotStockItem {
    rank: number;
    source_rank: number;
    rank_change: number;
    code: string;
    name: string;
    price: number;
    pct: number;
    heat_label: string;
    concepts: string[];
    amount_yi: number;
}

interface RankingPayload {
    items?: HotStockItem[];
    updated_at?: string;
    note?: string;
    degraded?: boolean;
    cache_hit?: boolean;
}

interface ChartPayload {
    points?: HotStockChartPoint[];
    previous_close?: number;
    data_date?: string;
    source?: string;
    degraded?: boolean;
}

function formatPct(value: number) {
    if (!Number.isFinite(value)) return '--';
    return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function formatTime(value?: string) {
    if (!value) return '--';
    return value.replace('T', ' ').slice(0, 16);
}

function pctTone(value: number) {
    if (value > 0) return 'text-red-600';
    if (value < 0) return 'text-teal-700';
    return 'text-slate-500';
}

export default function HotStocksView({ onOpenStock }: { onOpenStock: (stock: { code: string; name: string }) => void }) {
    const [rankPeriod, setRankPeriod] = React.useState<RankPeriod>('hour');
    const [chartPeriod, setChartPeriod] = React.useState<ChartPeriod>('minute');
    const [ranking, setRanking] = React.useState<RankingPayload | null>(null);
    const [selectedCode, setSelectedCode] = React.useState('');
    const [chart, setChart] = React.useState<ChartPayload | null>(null);
    const [rankingLoading, setRankingLoading] = React.useState(true);
    const [chartLoading, setChartLoading] = React.useState(false);
    const [error, setError] = React.useState('');
    const [chartRefresh, setChartRefresh] = React.useState(0);
    const forceChartRefresh = React.useRef(false);

    const fetchRanking = React.useCallback(async (forceRefresh = false) => {
        setRankingLoading(true);
        setError('');
        try {
            const response = await api.get('/api/market/hot-stocks', {
                params: { period: rankPeriod, limit: 30, force_refresh: forceRefresh },
            });
            const payload = response.data as RankingPayload;
            const items = payload.items || [];
            setRanking(payload);
            setSelectedCode((current) => items.some((item) => item.code === current) ? current : (items[0]?.code || ''));
        } catch (requestError) {
            const detail = (requestError as { response?: { data?: { detail?: string } } }).response?.data?.detail;
            setError(detail || '热度榜加载失败，请稍后重试');
        } finally {
            setRankingLoading(false);
        }
    }, [rankPeriod]);

    React.useEffect(() => {
        fetchRanking(false);
    }, [fetchRanking]);

    React.useEffect(() => {
        if (!selectedCode) {
            setChart(null);
            return;
        }
        const controller = new AbortController();
        const forceRefresh = forceChartRefresh.current;
        forceChartRefresh.current = false;
        const fetchChart = async () => {
            setChartLoading(true);
            try {
                const response = await api.get(`/api/market/hot-stocks/${selectedCode}/chart`, {
                    params: { period: chartPeriod, force_refresh: forceRefresh },
                    signal: controller.signal,
                });
                setChart(response.data as ChartPayload);
            } catch (requestError) {
                if ((requestError as { code?: string }).code !== 'ERR_CANCELED') setChart({ points: [], degraded: true });
            } finally {
                if (!controller.signal.aborted) setChartLoading(false);
            }
        };
        fetchChart();
        return () => controller.abort();
    }, [selectedCode, chartPeriod, chartRefresh]);

    const items = ranking?.items || [];
    const selected = items.find((item) => item.code === selectedCode) || items[0];
    const chartPoints = chart?.points || [];
    const handleRefresh = () => {
        fetchRanking(true);
        forceChartRefresh.current = true;
        setChartRefresh((value) => value + 1);
    };

    return (
        <section className="space-y-4" aria-labelledby="hot-stocks-title">
            <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <div className="flex items-center gap-2.5">
                        <span className="flex size-10 items-center justify-center rounded-xl bg-amber-100 text-amber-700">
                            <Flame size={21} aria-hidden="true" />
                        </span>
                        <div>
                            <h1 id="hot-stocks-title" className="page-heading">市场热股排行</h1>
                            <p className="page-description">点击股票查看最新分时或日 K；热度仅用于发现线索，不代表交易建议。</p>
                        </div>
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="inline-flex rounded-xl border border-slate-200 bg-white p-1" aria-label="榜单周期">
                        {([['hour', '小时榜'], ['day', '日榜']] as const).map(([value, label]) => (
                            <button
                                key={value}
                                type="button"
                                aria-pressed={rankPeriod === value}
                                onClick={() => setRankPeriod(value)}
                                className={cn(
                                    'min-h-10 rounded-lg px-4 text-sm font-semibold transition-[color,background-color,transform] active:scale-[0.96]',
                                    rankPeriod === value ? 'bg-teal-700 text-white shadow-sm' : 'text-slate-500 hover:bg-slate-50 hover:text-slate-900',
                                )}
                            >
                                {label}
                            </button>
                        ))}
                    </div>
                    <button type="button" onClick={handleRefresh} disabled={rankingLoading} className="toolbar-button" aria-label="刷新热股榜与图表">
                        {rankingLoading ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}
                        刷新
                    </button>
                </div>
            </header>

            {error && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-700" role="alert">
                    {error}
                </div>
            )}

            <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(420px,0.9fr)_minmax(0,1.6fr)]">
                <div className="workspace-panel min-w-0 overflow-hidden">
                    <div className="grid grid-cols-[2.5rem_minmax(7rem,1.2fr)_5.5rem_minmax(7rem,0.8fr)] items-center gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-3 text-xs font-semibold text-slate-500">
                        <span>#</span><span>名称</span><span className="text-end">涨跌</span><span className="text-end">热度</span>
                    </div>
                    <div className="max-h-[680px] overflow-y-auto" aria-busy={rankingLoading}>
                        {rankingLoading && items.length === 0 ? (
                            <div className="flex min-h-64 items-center justify-center gap-2 text-sm font-semibold text-slate-400">
                                <Loader2 size={18} className="animate-spin" aria-hidden="true" />正在获取市场热度
                            </div>
                        ) : items.length === 0 ? (
                            <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
                                <Signal size={26} className="text-slate-300" aria-hidden="true" />
                                <p className="mt-3 text-sm font-semibold text-slate-600">当前没有可用榜单</p>
                                <p className="mt-1 text-xs text-slate-400">可能是数据源暂时不可用，请稍后刷新。</p>
                            </div>
                        ) : items.map((item) => (
                            <button
                                key={item.code}
                                type="button"
                                aria-pressed={selected?.code === item.code}
                                onClick={() => setSelectedCode(item.code)}
                                className={cn(
                                    'grid min-h-[72px] w-full grid-cols-[2.5rem_minmax(7rem,1.2fr)_5.5rem_minmax(7rem,0.8fr)] items-center gap-2 border-b border-slate-100 px-4 py-3 text-left transition-[background-color,transform] last:border-b-0 active:scale-[0.99]',
                                    selected?.code === item.code ? 'bg-teal-50 ring-1 ring-inset ring-teal-200' : 'bg-white hover:bg-slate-50',
                                )}
                            >
                                <span className={cn('font-mono text-sm font-semibold tabular-nums', item.rank <= 3 ? 'text-amber-700' : 'text-slate-400')}>{item.rank}</span>
                                <span className="min-w-0">
                                    <span className="block truncate text-sm font-bold text-slate-900">{item.name}</span>
                                    <span className="mt-0.5 block font-mono text-xs tabular-nums text-slate-400">{item.code}</span>
                                    {item.concepts.length > 0 && <span className="mt-1 inline-flex max-w-full truncate rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">{item.concepts[0]}</span>}
                                </span>
                                <span className={cn('text-end font-mono text-sm font-bold tabular-nums', pctTone(item.pct))}>{formatPct(item.pct)}</span>
                                <span className="text-end text-xs font-semibold leading-5 text-slate-600">{item.heat_label}</span>
                            </button>
                        ))}
                    </div>
                </div>

                <div className="workspace-panel min-w-0 overflow-hidden">
                    <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
                        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                            <div className="min-w-0">
                                <div className="mb-3 inline-flex rounded-xl bg-slate-100 p-1" aria-label="图表周期">
                                    {([['minute', '分时'], ['day', '日 K']] as const).map(([value, label]) => (
                                        <button
                                            key={value}
                                            type="button"
                                            aria-pressed={chartPeriod === value}
                                            onClick={() => setChartPeriod(value)}
                                            className={cn(
                                                'min-h-10 rounded-lg px-4 text-sm font-semibold transition-[color,background-color,transform] active:scale-[0.96]',
                                                chartPeriod === value ? 'bg-white text-teal-800 shadow-sm' : 'text-slate-500 hover:text-slate-900',
                                            )}
                                        >{label}</button>
                                    ))}
                                </div>
                                {selected ? (
                                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                                        <h2 className="text-xl font-bold text-slate-950 sm:text-2xl">{selected.name}</h2>
                                        <span className="font-mono text-sm tabular-nums text-slate-400">{selected.code}</span>
                                        <span className={cn('font-mono text-2xl font-bold tabular-nums', pctTone(selected.pct))}>{selected.price > 0 ? selected.price.toFixed(2) : '--'}</span>
                                        <span className={cn('font-mono text-sm font-bold tabular-nums', pctTone(selected.pct))}>{formatPct(selected.pct)}</span>
                                    </div>
                                ) : <h2 className="text-lg font-bold text-slate-500">请选择股票</h2>}
                            </div>
                            {selected && (
                                <div className="flex flex-wrap items-center gap-3 lg:justify-end">
                                    <div className="text-xs text-slate-500">
                                        <span className="block">榜单序位</span>
                                        <span className="mt-0.5 block font-mono font-semibold tabular-nums text-slate-800">#{selected.rank}</span>
                                    </div>
                                    <div className="text-xs text-slate-500">
                                        <span className="block">图表日期</span>
                                        <span className="mt-0.5 block font-mono font-semibold tabular-nums text-slate-800">{chart?.data_date || '--'}</span>
                                    </div>
                                    <button type="button" onClick={() => onOpenStock({ code: selected.code, name: selected.name })} className="toolbar-button">
                                        完整分析 <ArrowUpRight size={15} aria-hidden="true" />
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="relative min-h-[360px] bg-white px-2 py-3 sm:min-h-[460px] sm:px-4 xl:min-h-[540px]">
                        {chartLoading && (
                            <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/75 text-sm font-semibold text-slate-500" role="status">
                                <Loader2 size={18} className="me-2 animate-spin" aria-hidden="true" />正在加载{chartPeriod === 'minute' ? '分时' : '日 K'}数据
                            </div>
                        )}
                        {!chartLoading && chartPoints.length === 0 ? (
                            <div className="flex min-h-[340px] flex-col items-center justify-center text-center text-slate-400 sm:min-h-[440px] xl:min-h-[520px]">
                                <TrendingUp size={30} aria-hidden="true" />
                                <p className="mt-3 text-sm font-semibold text-slate-600">暂无{chartPeriod === 'minute' ? '分时' : '日 K'}数据</p>
                                <p className="mt-1 text-xs">非交易时段会展示最近一个交易日，数据缺失时可稍后刷新。</p>
                            </div>
                        ) : (
                            <HotStockChart period={chartPeriod} points={chartPoints} previousClose={chart?.previous_close} />
                        )}
                    </div>
                    <footer className="flex flex-col gap-1 border-t border-slate-100 bg-slate-50/70 px-4 py-3 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
                        <span>{ranking?.note || '热度榜用于发现市场关注线索，不构成交易建议。'}</span>
                        <span className="shrink-0 font-mono tabular-nums">更新 {formatTime(ranking?.updated_at)}{ranking?.cache_hit ? ' · 缓存' : ''}{ranking?.degraded ? ' · 降级数据' : ''}</span>
                    </footer>
                </div>
            </div>
        </section>
    );
}
