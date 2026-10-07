"use client";

import React from 'react';
import { AlertTriangle, Flame, Loader2, RefreshCw, Users } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

type ThemeScope = 'CONCEPT' | 'INDUSTRY';

interface MarketEnvironment {
    stage?: string | null;
    confidence?: number | null;
    as_of?: string | null;
    mom_10d_pct?: number | null;
    gate_blocked?: boolean | null;
    zt_broken_rate?: number | null;
    zt_max_streak?: number | null;
    zt_bar_date?: string | null;
    summary?: string | null;
    summary_llm?: string | null;
}

interface ThemeItem {
    theme: string;
    heat?: number | null;
    rank?: number | null;
    trend?: 'new' | 'up' | 'down' | 'flat' | string | null;
    flow_3d?: number | null;
    flow_today?: number | null;
    chg_today?: number | null;
    limit_up_count?: number | null;
    max_streak?: number | null;
    hot_overlap?: number | null;
    pct_above_ma20?: number | null;
    members_count?: number | null;
    narrative?: string | null;
    narrative_llm?: string | null;
    evidence?: string[] | null;
    tier?: string | null;
    members?: string[] | null;
}

interface ThemeHeatPayload {
    market_environment?: MarketEnvironment | null;
    bar_date?: string | null;
    scope?: string | null;
    themes?: ThemeItem[] | null;
}

interface MembersPayload {
    theme?: string | null;
    scope?: string | null;
    bar_date?: string | null;
    members?: string[] | null;
    narrative?: string | null;
    narrative_llm?: string | null;
}

const SCOPE_OPTIONS: ReadonlyArray<{ value: ThemeScope; label: string }> = [
    { value: 'CONCEPT', label: '概念' },
    { value: 'INDUSTRY', label: '行业' },
];

const TREND_META: Record<string, { label: string; className: string }> = {
    new: { label: '新', className: 'bg-blue-50 text-blue-700 border-blue-100' },
    up: { label: '升', className: 'bg-rose-50 text-rose-700 border-rose-100' },
    down: { label: '降', className: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
    flat: { label: '平', className: 'bg-slate-100 text-slate-500 border-slate-200' },
};

// A股习惯：涨红跌绿（正值玫瑰/红、负值绿）
function toneBySign(value?: number | null) {
    if (value == null || !Number.isFinite(value)) return 'text-slate-500';
    if (value > 0) return 'text-rose-600';
    if (value < 0) return 'text-emerald-600';
    return 'text-slate-500';
}

function formatNum(value?: number | null, digits = 1, suffix = '') {
    if (value == null || !Number.isFinite(value)) return '--';
    return `${value.toFixed(digits)}${suffix}`;
}

function formatFlow(value?: number | null) {
    if (value == null || !Number.isFinite(value)) return '--';
    return `${value > 0 ? '+' : ''}${value.toFixed(1)} 亿`;
}

function formatPct(value?: number | null) {
    if (value == null || !Number.isFinite(value)) return '--';
    return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function stageTone(stage?: string | null) {
    if (!stage) return 'bg-slate-100 border-slate-200 text-slate-600';
    if (stage.includes('退潮')) return 'bg-amber-50 border-amber-200 text-amber-700';
    if (stage.includes('普跌')) return 'bg-rose-50 border-rose-200 text-rose-700';
    if (stage.includes('可交易')) return 'bg-emerald-50 border-emerald-200 text-emerald-700';
    return 'bg-slate-100 border-slate-200 text-slate-600';
}

function tierTone(tier?: string | null) {
    if (!tier) return 'bg-slate-100 text-slate-500';
    if (tier.includes('主线')) return 'bg-rose-50 text-rose-600';
    if (tier.includes('扩散')) return 'bg-amber-50 text-amber-700';
    if (tier.includes('观察')) return 'bg-blue-50 text-blue-700';
    return 'bg-slate-100 text-slate-500';
}

export default function ThemeHeatView({ onOpenStock }: { onOpenStock?: (stock: { code: string; name: string }) => void }) {
    const [scope, setScope] = React.useState<ThemeScope>('CONCEPT');
    const [date, setDate] = React.useState('');
    const [heat, setHeat] = React.useState<ThemeHeatPayload | null>(null);
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState('');
    const [selectedTheme, setSelectedTheme] = React.useState('');

    const [members, setMembers] = React.useState<string[] | null>(null);
    const [membersTheme, setMembersTheme] = React.useState('');
    const [membersLoading, setMembersLoading] = React.useState(false);
    const [membersError, setMembersError] = React.useState('');

    const fetchHeat = React.useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const response = await api.get('/api/themes/heat', {
                params: { scope, ...(date ? { date } : {}) },
            });
            const payload = response.data as ThemeHeatPayload;
            setHeat(payload);
            const themes = payload.themes || [];
            setSelectedTheme((current) => themes.some((item) => item.theme === current) ? current : (themes[0]?.theme || ''));
            setMembers(null);
            setMembersTheme('');
            setMembersError('');
        } catch (requestError) {
            const detail = (requestError as { response?: { data?: { detail?: string } } }).response?.data?.detail;
            setError(detail || '题材热度加载失败，请稍后重试');
            setHeat(null);
        } finally {
            setLoading(false);
        }
    }, [scope, date]);

    React.useEffect(() => { fetchHeat(); }, [fetchHeat]);

    const themes = heat?.themes || [];
    const selected = themes.find((item) => item.theme === selectedTheme) || null;

    const fetchMembers = async () => {
        if (!selected) return;
        setMembersLoading(true);
        setMembersError('');
        try {
            const response = await api.get('/api/themes/members', {
                params: { scope, theme: selected.theme, ...(date ? { date } : {}) },
            });
            const payload = response.data as MembersPayload;
            setMembers(payload.members || []);
            setMembersTheme(payload.theme || selected.theme);
        } catch {
            setMembers(null);
            setMembersError('成分股列表加载失败，请稍后重试');
        } finally {
            setMembersLoading(false);
        }
    };

    const handleSelectTheme = (theme: string) => {
        if (theme === selectedTheme) return;
        setSelectedTheme(theme);
        setMembers(null);
        setMembersTheme('');
        setMembersError('');
    };

    return (
        <section className="space-y-4" aria-labelledby="theme-heat-title">
            <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div className="flex items-center gap-2.5">
                    <span className="flex size-10 items-center justify-center rounded-xl bg-rose-100 text-rose-700">
                        <Flame size={21} aria-hidden="true" />
                    </span>
                    <div>
                        <h1 id="theme-heat-title" className="page-heading">题材热点</h1>
                        <p className="page-description">追踪概念/行业热度、资金与新闻证据；SHADOW 观察数据，不构成交易指令。</p>
                    </div>
                </div>
                <button type="button" onClick={fetchHeat} disabled={loading} className="toolbar-button">
                    <RefreshCw size={16} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
                    刷新
                </button>
            </header>

            {error && (
                <div className="flex items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3" role="alert">
                    <div className="flex items-center gap-2 text-sm font-bold text-rose-800">
                        <AlertTriangle size={16} aria-hidden="true" />
                        <span>{error}</span>
                    </div>
                    <button
                        type="button"
                        onClick={fetchHeat}
                        className="rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-black text-rose-700 hover:bg-rose-100"
                    >
                        重试
                    </button>
                </div>
            )}

            {heat?.market_environment && (
                <div className="workspace-panel flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex min-w-0 items-center gap-3">
                        <span className={cn('inline-flex shrink-0 items-center rounded-full border px-3 py-1 text-xs font-bold', stageTone(heat.market_environment.stage))}>
                            {heat.market_environment.stage || '未知'}
                        </span>
                        <p className="min-w-0 text-sm font-semibold leading-relaxed text-slate-600">
                            {heat.market_environment.summary_llm || heat.market_environment.summary || '暂无市场状态摘要'}
                        </p>
                    </div>
                    <span className="shrink-0 text-xs font-bold text-slate-400">
                        截至 {heat.market_environment.as_of || heat.bar_date || '--'}
                    </span>
                </div>
            )}

            <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(360px,0.9fr)_minmax(0,1.6fr)]">
                {/* 左栏：scope 切换 + 日期 + 主题列表 */}
                <div className="workspace-panel min-w-0 overflow-hidden">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-3">
                        <div className="inline-flex rounded-xl border border-slate-200 bg-white p-1" role="group" aria-label="题材范围">
                            {SCOPE_OPTIONS.map((option) => (
                                <button
                                    key={option.value}
                                    type="button"
                                    aria-pressed={scope === option.value}
                                    onClick={() => setScope(option.value)}
                                    className={cn(
                                        'min-h-9 rounded-lg px-3.5 text-sm font-semibold transition-[color,background-color]',
                                        scope === option.value ? 'bg-rose-600 text-white shadow-sm' : 'text-slate-500 hover:bg-slate-50 hover:text-slate-900',
                                    )}
                                >
                                    {option.label}
                                </button>
                            ))}
                        </div>
                        <label className="flex items-center gap-2">
                            <span className="metric-label">日期</span>
                            <input
                                type="date"
                                value={date}
                                onChange={(event) => setDate(event.target.value)}
                                aria-label="热度数据日期，留空为最新"
                                className="min-h-9 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm font-medium text-slate-700 transition-[border-color,box-shadow] focus:border-rose-400 focus:outline-none focus:ring-4 focus:ring-rose-500/10"
                            />
                        </label>
                    </div>
                    <div className="max-h-[640px] overflow-y-auto" aria-busy={loading}>
                        {loading && themes.length === 0 ? (
                            <div className="flex min-h-64 items-center justify-center gap-2 text-sm font-semibold text-slate-400" role="status">
                                <Loader2 size={18} className="animate-spin" aria-hidden="true" />正在获取题材热度
                            </div>
                        ) : themes.length === 0 ? (
                            <div className="flex min-h-64 flex-col items-center justify-center px-6 text-center">
                                <Flame size={26} className="text-slate-300" aria-hidden="true" />
                                <p className="mt-3 text-sm font-semibold text-slate-600">热度榜尚未采集</p>
                                <p className="mt-1 text-xs text-slate-400">任务 11:45/15:20 运行后生成，请稍后再来。</p>
                            </div>
                        ) : (
                            themes.map((item) => {
                                const trend = TREND_META[item.trend || ''] || TREND_META.flat;
                                const active = item.theme === selectedTheme;
                                return (
                                    <button
                                        key={item.theme}
                                        type="button"
                                        aria-pressed={active}
                                        onClick={() => handleSelectTheme(item.theme)}
                                        className={cn(
                                            'flex w-full items-center gap-3 border-b border-slate-100 px-4 py-3 text-left transition-[background-color] last:border-b-0',
                                            active ? 'bg-rose-50/70 ring-1 ring-inset ring-rose-200' : 'bg-white hover:bg-slate-50',
                                        )}
                                    >
                                        <span className={cn('w-6 shrink-0 text-center font-mono text-sm font-bold tabular-nums', item.rank != null && item.rank <= 3 ? 'text-rose-600' : 'text-slate-400')}>
                                            {item.rank ?? '--'}
                                        </span>
                                        <span className="min-w-0 flex-1">
                                            <span className="flex items-center gap-2">
                                                <span className={cn('truncate text-sm font-bold', active ? 'text-rose-700' : 'text-slate-900')}>{item.theme}</span>
                                                <span className={cn('shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-black leading-none', trend.className)}>
                                                    {trend.label}
                                                </span>
                                            </span>
                                            <span className={cn('mt-1 block text-xs font-semibold tabular-nums', toneBySign(item.flow_3d))}>
                                                3日资金 {formatFlow(item.flow_3d)}
                                            </span>
                                        </span>
                                        <span className="shrink-0 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-black tabular-nums text-rose-600">
                                            {formatNum(item.heat, 1)}
                                        </span>
                                    </button>
                                );
                            })
                        )}
                    </div>
                </div>

                {/* 右栏：选中主题详情 */}
                <div className="workspace-panel min-w-0 p-4 sm:p-5">
                    {selected ? (
                        <div className="space-y-4">
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                                <h2 className="text-xl font-black text-slate-950 sm:text-2xl">{selected.theme}</h2>
                                {selected.tier && (
                                    <span className={cn('rounded-full px-2.5 py-1 text-xs font-black', tierTone(selected.tier))}>{selected.tier}</span>
                                )}
                                <span className="ms-auto text-xs font-bold text-slate-400">
                                    数据日期 {heat?.bar_date || '--'} · 成分 {selected.members_count ?? '--'} 家
                                </span>
                            </div>

                            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                                <MetricCard
                                    label="3日资金(亿)"
                                    value={formatFlow(selected.flow_3d)}
                                    valueClass={toneBySign(selected.flow_3d)}
                                />
                                <MetricCard
                                    label="当日涨跌"
                                    value={formatPct(selected.chg_today)}
                                    valueClass={toneBySign(selected.chg_today)}
                                />
                                <MetricCard
                                    label="站上MA20"
                                    value={selected.pct_above_ma20 == null ? '--' : `${formatNum(selected.pct_above_ma20, 1)}%`}
                                />
                                <MetricCard
                                    label="人气重叠"
                                    value={
                                        selected.hot_overlap == null
                                            ? '--'
                                            : selected.members_count
                                                ? `${Math.round((selected.hot_overlap / selected.members_count) * 100)}%`
                                                : String(selected.hot_overlap)
                                    }
                                    sub={
                                        selected.hot_overlap != null && selected.members_count
                                            ? `${selected.hot_overlap} / ${selected.members_count}`
                                            : undefined
                                    }
                                />
                            </div>

                            {(selected.limit_up_count != null || selected.max_streak != null || selected.flow_today != null) && (
                                <p className="text-xs font-semibold text-slate-500">
                                    当日资金 {formatFlow(selected.flow_today)}
                                    {selected.limit_up_count != null ? ` · 涨停 ${selected.limit_up_count} 家` : ''}
                                    {selected.max_streak != null ? ` · 最高连板 ${selected.max_streak}` : ''}
                                </p>
                            )}

                            <div className="rounded-xl border border-slate-200 bg-white p-4">
                                <p className="metric-label">
                                    理由
                                    {selected.narrative_llm && (
                                        <span className="ml-2 rounded bg-indigo-100 px-1.5 py-0.5 text-[9px] font-black text-indigo-600 align-middle">AI</span>
                                    )}
                                </p>
                                <p className="mt-1.5 text-sm font-semibold leading-relaxed text-slate-700">
                                    {selected.narrative_llm || selected.narrative || '--'}
                                </p>
                            </div>

                            <div className="rounded-xl border border-slate-200 bg-white p-4">
                                <p className="metric-label">新闻证据</p>
                                {(selected.evidence || []).length > 0 ? (
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        {(selected.evidence || []).map((title, index) => (
                                            <span
                                                key={`${title}-${index}`}
                                                className="max-w-full truncate rounded-lg bg-slate-100 px-2.5 py-1.5 text-xs font-semibold text-slate-600"
                                                title={title}
                                            >
                                                {title}
                                            </span>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="mt-1.5 text-sm font-semibold text-slate-400">--</p>
                                )}
                            </div>

                            <div className="rounded-xl border border-slate-200 bg-white p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <p className="metric-label">成分股</p>
                                    <button type="button" onClick={fetchMembers} disabled={membersLoading} className="toolbar-button">
                                        {membersLoading
                                            ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                                            : <Users size={15} aria-hidden="true" />}
                                        {membersTheme === selected.theme && members ? '刷新成分股' : '加载成分股'}
                                    </button>
                                </div>
                                {membersError && (
                                    <p className="mt-2 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700" role="alert">
                                        {membersError}
                                    </p>
                                )}
                                {membersTheme === selected.theme && members && members.length > 0 ? (
                                    <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
                                        {members.map((code) => (
                                            <button
                                                key={code}
                                                type="button"
                                                onClick={() => onOpenStock?.({ code, name: code })}
                                                className="min-h-9 truncate rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 font-mono text-xs font-bold tabular-nums text-slate-700 transition-colors hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700"
                                                aria-label={`打开个股 ${code}`}
                                            >
                                                {code}
                                            </button>
                                        ))}
                                    </div>
                                ) : membersTheme === selected.theme && members && members.length === 0 ? (
                                    <p className="mt-2 text-xs font-semibold text-slate-400">该题材暂无成分股数据</p>
                                ) : !membersLoading && !membersError ? (
                                    <p className="mt-2 text-xs font-semibold text-slate-400">点击右上角按钮拉取题材成分股列表</p>
                                ) : null}
                            </div>
                        </div>
                    ) : (
                        <div className="flex min-h-[320px] flex-col items-center justify-center gap-2 text-center">
                            <Flame size={28} className="text-slate-300" aria-hidden="true" />
                            <p className="text-sm font-semibold text-slate-600">
                                {loading ? '正在加载题材热度…' : themes.length === 0 ? '暂无题材数据' : '在左侧选择一个题材查看详情'}
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}

function MetricCard({ label, value, valueClass, sub }: { label: string; value: string; valueClass?: string; sub?: string }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <p className="text-[10px] font-black text-slate-400">{label}</p>
            <p className={cn('mt-1 font-mono text-lg font-black tabular-nums text-slate-800', valueClass)}>{value}</p>
            {sub && <p className="text-[10px] font-bold tabular-nums text-slate-400">{sub}</p>}
        </div>
    );
}
