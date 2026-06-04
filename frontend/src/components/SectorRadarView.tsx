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
    leaders: SectorLeader[];
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
    const [updatedAt, setUpdatedAt] = React.useState('');
    const [cacheHit, setCacheHit] = React.useState(false);
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState('');

    const fetchData = React.useCallback(async (force = false) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.get(`/api/market/sector-strength?limit=30${force ? '&force=true' : ''}`);
            setItems(res.data?.items || []);
            setUpdatedAt(res.data?.updated_at || '');
            setCacheHit(Boolean(res.data?.cache_hit));
            if (res.data?.error) setError(res.data.error);
        } catch (err: any) {
            setError(err?.response?.data?.detail || err?.message || '板块数据加载失败');
        } finally {
            setLoading(false);
        }
    }, []);

    React.useEffect(() => {
        fetchData();
    }, [fetchData]);

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
                        先识别强势板块，再从板块前排中筛选个股
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
                            <div key={item.industry} className="px-5 py-4 grid grid-cols-[80px_1.2fr_1fr_1.3fr] gap-4 items-center hover:bg-slate-50/70">
                                <div className="text-2xl font-black text-slate-300">#{item.sector_rank}</div>
                                <div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-sm font-black text-slate-900">{item.industry}</span>
                                        <span className={cn("px-2 py-0.5 rounded border text-[10px] font-black", phaseTone[item.sector_phase] || phaseTone.SECTOR_NEUTRAL)}>
                                            {phaseLabel[item.sector_phase] || item.sector_phase}
                                        </span>
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
                                    {item.leaders?.slice(0, 5).map(leader => (
                                        <span key={leader.code} className="rounded-md bg-white border border-slate-100 px-2 py-1 text-[10px] font-bold text-slate-600">
                                            {leader.name}
                                            {leader.role && <span className="ml-1 text-slate-400">{roleLabel[leader.role] || leader.role}</span>}
                                            <span className={leader.pct >= 0 ? 'text-rose-500' : 'text-emerald-600'}> {leader.pct >= 0 ? '+' : ''}{leader.pct}%</span>
                                        </span>
                                    ))}
                                </div>
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

function formatPct(value?: number) {
    if (value === undefined || value === null) return '--';
    return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
}
