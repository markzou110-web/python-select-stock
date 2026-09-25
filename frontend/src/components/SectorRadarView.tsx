"use client";

import React from 'react';
import { Activity, Loader2, RefreshCw, TrendingUp, Users } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface SectorLeader {
    code: string;
    name: string;
    price: number;
    pct: number;
    role?: string;
    relative_strength_5d?: number;
    lead_consistency?: number;
    leader_score?: number;
}

interface SectorStrengthItem {
    industry: string;
    sector_momentum_score: number;
    sector_breadth: number;
    sector_avg_pct: number;
    sector_hot_count: number;
    sector_limit_count: number;
    sector_total_count: number;
    sector_up_count: number;
    sector_phase: string;
    sector_rank: number;
    sector_3d_pct?: number;
    sector_5d_pct?: number;
    sector_consecutive_up_days?: number;
    sector_trend_slope?: number;
    sector_prev_month_pct?: number;
    sector_prev_month_rank?: number;
    sector_prev_month_top5?: boolean;
    sector_prev_month_period?: string;
    leaders: SectorLeader[];
}

interface SectorPushGapItem {
    industry: string;
    has_push_candidate?: boolean;
    push_candidate_count?: number;
    scan_candidate_count?: number;
    primary_reason_label?: string;
    representative_candidates?: SectorGapCandidate[];
}

interface SectorGapCandidate {
    code: string;
    name?: string;
    industry?: string;
    price?: number;
    pct?: number;
    score?: number;
    trade_bucket?: string;
    reason_label?: string;
    blockers?: string[];
}

const phaseLabel: Record<string, string> = {
    SECTOR_CONFIRM: '趋势确认',
    SECTOR_EARLY: '早期启动',
    SECTOR_CLIMAX: '高潮风险',
    SECTOR_NEUTRAL: '中性观察',
    SECTOR_FADE: '扩散转弱',
};

const phaseTone: Record<string, string> = {
    SECTOR_CONFIRM: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    SECTOR_EARLY: 'bg-sky-50 text-sky-700 border-sky-100',
    SECTOR_CLIMAX: 'bg-orange-50 text-orange-700 border-orange-100',
    SECTOR_NEUTRAL: 'bg-slate-50 text-slate-600 border-slate-100',
    SECTOR_FADE: 'bg-rose-50 text-rose-700 border-rose-100',
};

const roleLabel: Record<string, string> = {
    LEADER: '龙头',
    CORE: '中军',
    FOLLOWER: '后排',
    LAGGARD: '掉队',
};

export default function SectorRadarView() {
    const [items, setItems] = React.useState<SectorStrengthItem[]>([]);
    const [gapMap, setGapMap] = React.useState<Record<string, SectorPushGapItem>>({});
    const [updatedAt, setUpdatedAt] = React.useState('');
    const [cacheHit, setCacheHit] = React.useState(false);
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState('');
    const [watchStatus, setWatchStatus] = React.useState<Record<string, string>>({});

    const fetchData = React.useCallback(async (force = false) => {
        setLoading(true);
        setError('');
        try {
            const [strengthRes, gapsRes] = await Promise.all([
                api.get(`/api/market/sector-strength?limit=30${force ? '&force=true' : ''}`),
                api.get(`/api/market/sector-push-gaps?limit=30${force ? '&force=true' : ''}`),
            ]);
            setItems(strengthRes.data?.items || []);
            setUpdatedAt(strengthRes.data?.updated_at || '');
            setCacheHit(Boolean(strengthRes.data?.cache_hit));
            const gaps = (gapsRes.data?.items || []) as SectorPushGapItem[];
            setGapMap(Object.fromEntries(gaps.map(item => [item.industry, item])));
            if (strengthRes.data?.error) setError(strengthRes.data.error);
        } catch (err) {
            const e = err as { response?: { data?: { detail?: string } }; message?: string };
            setError(e?.response?.data?.detail || e?.message || '板块数据加载失败');
        } finally {
            setLoading(false);
        }
    }, []);

    React.useEffect(() => {
        fetchData();
    }, [fetchData]);

    const addCandidateToWatchlist = React.useCallback(async (candidate: SectorGapCandidate, industry: string) => {
        if (!candidate.code || !candidate.price || candidate.price <= 0) {
            setWatchStatus(prev => ({ ...prev, [candidate.code]: '缺少价格' }));
            return;
        }
        setWatchStatus(prev => ({ ...prev, [candidate.code]: '加入中' }));
        try {
            await api.post('/api/watchlist/add', {
                code: candidate.code,
                name: candidate.name || candidate.code,
                industry: candidate.industry || industry,
                watch_price: candidate.price,
                strategy_type: 'sector_watch',
                reason: candidate.reason_label || '热门板块未推候选，加入观察池跟踪',
                source: 'sector_push_gap',
                theme: candidate.industry || industry,
                rise_logic: candidate.reason_label || '强题材观察，等待买点确认',
                invalidation: '板块转弱、个股跌破支撑或买点结构失效',
            });
            setWatchStatus(prev => ({ ...prev, [candidate.code]: '已加入' }));
        } catch {
            setWatchStatus(prev => ({ ...prev, [candidate.code]: '加入失败' }));
        }
    }, []);

    const top = items[0];

    return (
        <div className="space-y-4">
            <div className="workspace-panel px-5 py-4 flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-black text-slate-900 flex items-center gap-2">
                        <Activity size={20} className="text-indigo-500" />
                        板块雷达
                    </h2>
                    <p className="text-xs font-bold text-slate-400 mt-1">
                        上月前5板块定方向，实时强度二次确认，每个板块保留前2只龙头
                    </p>
                </div>
                <button
                    onClick={() => fetchData(true)}
                    className="h-9 px-3 rounded-md bg-slate-900 text-white text-xs font-black flex items-center gap-2"
                >
                    {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                    刷新
                </button>
            </div>

            {top && (
                <div className="grid grid-cols-4 gap-3">
                    <Metric label="最强板块" value={top.industry} note={phaseLabel[top.sector_phase] || '中性'} />
                    <Metric label="板块强度" value={top.sector_momentum_score.toFixed(0)} note={`排名 #${top.sector_rank}`} />
                    <Metric label="扩散率" value={`${top.sector_breadth.toFixed(0)}%`} note={`${top.sector_up_count}/${top.sector_total_count} 上涨`} />
                    <Metric label="3/5日趋势" value={`${formatPct(top.sector_3d_pct)} / ${formatPct(top.sector_5d_pct)}`} note={`连续上涨 ${top.sector_consecutive_up_days ?? 0} 天`} />
                </div>
            )}

            {error && (
                <div className="rounded-md border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-700">
                    {error}
                </div>
            )}

            <div className="workspace-panel overflow-hidden">
                <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
                    <span className="text-xs font-black text-slate-500 uppercase tracking-widest">实时板块强度榜</span>
                    <span className="text-[10px] font-bold text-slate-400">
                        {updatedAt ? new Date(updatedAt).toLocaleString() : '等待更新'}{cacheHit ? ' · 缓存' : ''}
                    </span>
                </div>

                {loading ? (
                    <div className="p-16 text-center text-slate-400 font-bold">
                        <Loader2 className="animate-spin inline mr-2" size={18} />
                        正在计算板块扩散率...
                    </div>
                ) : (
                    <div className="divide-y divide-slate-100">
                        {items.map(item => (
                            <div key={item.industry} className="px-5 py-4 hover:bg-slate-50/70">
                                <div className="grid grid-cols-[80px_1.2fr_1fr_1.3fr] gap-4 items-center">
                                    <div className="text-2xl font-black text-slate-300">#{item.sector_rank}</div>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <span className="text-sm font-black text-slate-900">{item.industry}</span>
                                            <span className={cn("px-2 py-0.5 rounded border text-[10px] font-black", phaseTone[item.sector_phase] || phaseTone.SECTOR_NEUTRAL)}>
                                                {phaseLabel[item.sector_phase] || item.sector_phase}
                                            </span>
                                            {item.sector_prev_month_rank != null && (
                                                <span className={cn(
                                                    "px-2 py-0.5 rounded border text-[10px] font-black",
                                                    item.sector_prev_month_top5
                                                        ? "bg-indigo-50 text-indigo-700 border-indigo-100"
                                                        : "bg-slate-50 text-slate-400 border-slate-100",
                                                )}>
                                                    上月 #{item.sector_prev_month_rank} · {formatPct(item.sector_prev_month_pct)}
                                                </span>
                                            )}
                                        </div>
                                        <div className="mt-1 flex items-center gap-3 text-[10px] font-bold text-slate-400">
                                            <span className="flex items-center gap-1"><TrendingUp size={11} /> 均涨 {item.sector_avg_pct >= 0 ? '+' : ''}{item.sector_avg_pct}%</span>
                                            <span className="flex items-center gap-1"><Users size={11} /> 扩散 {item.sector_breadth.toFixed(0)}%</span>
                                            <span>3日 {formatPct(item.sector_3d_pct)}</span>
                                            <span>5日 {formatPct(item.sector_5d_pct)}</span>
                                            <span>连涨 {item.sector_consecutive_up_days ?? 0}天</span>
                                        </div>
                                    </div>
                                    <div>
                                        <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                                            <div
                                                className={cn("h-full rounded-full", item.sector_momentum_score >= 75 ? "bg-emerald-500" : item.sector_momentum_score >= 58 ? "bg-sky-500" : "bg-slate-300")}
                                                style={{ width: `${Math.min(100, item.sector_momentum_score)}%` }}
                                            />
                                        </div>
                                        <div className="mt-1 text-[10px] font-black text-slate-500">强度 {item.sector_momentum_score.toFixed(1)} / 100</div>
                                    </div>
                                    <div className="flex flex-wrap gap-1.5 justify-end">
                                        {item.leaders?.slice(0, 2).map((leader, idx) => {
                                            const isTopLeader = idx === 0 && leader.leader_score != null && leader.leader_score >= 80;
                                            return (
                                                <span
                                                    key={leader.code}
                                                    title={leader.leader_score != null ? `龙头分 ${leader.leader_score} · 5日相对板块 ${leader.relative_strength_5d ?? 0 >= 0 ? '+' : ''}${leader.relative_strength_5d ?? 0}% · 领涨占比 ${leader.lead_consistency ?? 0}%` : undefined}
                                                    className={cn(
                                                        "rounded-md border px-2 py-1 text-[10px] font-bold",
                                                        isTopLeader
                                                            ? "bg-amber-50 border-amber-200 text-amber-700"
                                                            : "bg-white border-slate-100 text-slate-600"
                                                    )}
                                                >
                                                    {isTopLeader && <span className="mr-0.5">👑</span>}
                                                    {leader.name}
                                                    {leader.role && <span className="ml-1 text-slate-400">{roleLabel[leader.role] || leader.role}</span>}
                                                    <span className={leader.pct >= 0 ? 'text-rose-500' : 'text-emerald-600'}> {leader.pct >= 0 ? '+' : ''}{leader.pct}%</span>
                                                </span>
                                            );
                                        })}
                                    </div>
                                </div>
                                <SectorGapLine
                                    gap={gapMap[item.industry]}
                                    onAddWatch={candidate => addCandidateToWatchlist(candidate, item.industry)}
                                    watchStatus={watchStatus}
                                />
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
    return (
        <div className="workspace-panel px-4 py-3">
            <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{label}</div>
            <div className="mt-1 text-xl font-black text-slate-900 truncate">{value}</div>
            <div className="mt-1 text-xs font-bold text-slate-400">{note}</div>
        </div>
    );
}

function SectorGapLine({
    gap,
    onAddWatch,
    watchStatus,
}: {
    gap?: SectorPushGapItem;
    onAddWatch: (candidate: SectorGapCandidate) => void;
    watchStatus: Record<string, string>;
}) {
    if (!gap) {
        return null;
    }
    const hasPush = Boolean(gap.has_push_candidate);
    const candidates = gap.representative_candidates || [];
    return (
        <div className={cn(
            "mt-3 ml-[80px] rounded-md border px-3 py-2 text-xs font-bold",
            hasPush
                ? "border-emerald-100 bg-emerald-50/70 text-emerald-700"
                : "border-amber-100 bg-amber-50/70 text-amber-700"
        )}>
            {hasPush ? (
                <span>已进入推荐：{gap.push_candidate_count || 0} 只可推候选，扫描候选 {gap.scan_candidate_count || 0} 只</span>
            ) : (
                <>
                    <span>未推原因：{gap.primary_reason_label || '继续观察'} · 扫描候选 {gap.scan_candidate_count || 0} 只</span>
                    {candidates.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-2">
                            {candidates.slice(0, 3).map(candidate => {
                                const status = watchStatus[candidate.code];
                                return (
                                    <div key={candidate.code} className="flex items-center gap-2 rounded-md border border-amber-100 bg-white/70 px-2 py-1 text-[10px] text-slate-600">
                                        <span className="font-black text-slate-700">{candidate.name || candidate.code}</span>
                                        <span>{candidate.trade_bucket || '--'}</span>
                                        <span className={Number(candidate.pct || 0) >= 0 ? 'text-rose-500' : 'text-emerald-600'}>
                                            {Number(candidate.pct || 0) >= 0 ? '+' : ''}{Number(candidate.pct || 0).toFixed(2)}%
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => onAddWatch(candidate)}
                                            disabled={status === '加入中' || status === '已加入'}
                                            className={cn(
                                                "rounded bg-slate-900 px-2 py-0.5 font-black text-white disabled:bg-slate-300",
                                                status === '已加入' && "bg-emerald-500"
                                            )}
                                        >
                                            {status || '加入观察池'}
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

function formatPct(value?: number) {
    if (value === undefined || value === null) return '--';
    return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
}
