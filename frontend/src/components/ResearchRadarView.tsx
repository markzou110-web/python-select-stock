"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ExternalLink, FileText, Loader2, Newspaper, RefreshCw, Sparkles } from 'lucide-react';
import api, { getApiErrorDetail } from '@/lib/api';
import { cn } from '@/lib/utils';

type Evidence = {
    type?: string;
    kind?: 'RISK' | 'CATALYST' | 'INFO' | string;
    title?: string;
    date?: string;
    source?: string;
    url?: string;
};

type RadarItem = {
    code: string;
    name?: string;
    industry?: string;
    scopes?: string[];
    evidence?: Evidence[];
    errors?: string[];
};

type RadarPayload = {
    updated_at?: string;
    cache_hit?: boolean;
    policy?: string;
    summary?: { targets?: number; evidence?: number; risk?: number; catalyst?: number; partial_targets?: number };
    items?: RadarItem[];
};

export default function ResearchRadarView({ onOpenStock }: { onOpenStock: (stock: { code: string; name: string }) => void }) {
    const [data, setData] = useState<RadarPayload | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const fetchData = useCallback(async (forceRefresh = false) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.get('/api/market/research-radar', {
                params: { limit: 12, force_refresh: forceRefresh },
            });
            setData(res.data);
        } catch (err: unknown) {
            setError(getApiErrorDetail(err) || '资讯雷达加载失败');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchData(false); }, [fetchData]);

    const summary = data?.summary || {};
    const items = data?.items || [];
    return (
        <div className="space-y-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <Newspaper size={20} className="text-indigo-600" />
                        <h2 className="text-xl font-black text-slate-900">候选资讯雷达</h2>
                    </div>
                    <p className="mt-1 text-xs font-bold text-slate-500">只追踪持仓、观察池和最新扫描候选的公告与新闻证据</p>
                </div>
                <button
                    type="button"
                    onClick={() => fetchData(true)}
                    disabled={loading}
                    className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-600 hover:text-indigo-600 disabled:opacity-50"
                >
                    <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新资讯
                </button>
            </div>

            <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
                <RadarStat label="关联标的" value={summary.targets || 0} />
                <RadarStat label="证据条目" value={summary.evidence || 0} />
                <RadarStat label="风险事件" value={summary.risk || 0} tone="risk" />
                <RadarStat label="催化事件" value={summary.catalyst || 0} tone="catalyst" />
                <RadarStat label="部分缺失" value={summary.partial_targets || 0} />
            </div>

            <div className="rounded-md border border-slate-200 bg-white/70 px-3 py-2 text-xs font-bold text-slate-500">
                {data?.policy || '研究证据只展示，不修改策略分或交易资格'}
                {data?.updated_at ? ` · 更新于 ${String(data.updated_at).slice(0, 19)}` : ''}
                {data?.cache_hit ? ' · 缓存' : ''}
            </div>

            {loading && !data ? (
                <div className="flex items-center justify-center gap-2 py-20 text-sm font-bold text-slate-400"><Loader2 className="animate-spin" size={18} />正在关联候选资讯</div>
            ) : error ? (
                <div className="rounded-md border border-rose-100 bg-rose-50 p-4 text-sm font-bold text-rose-700">{error}</div>
            ) : (
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                    {items.map(item => <RadarStock key={item.code} item={item} onOpenStock={onOpenStock} />)}
                    {!items.length && <div className="py-20 text-center text-sm font-bold text-slate-400">当前没有持仓、观察池或扫描候选</div>}
                </div>
            )}
        </div>
    );
}

function RadarStock({ item, onOpenStock }: { item: RadarItem; onOpenStock: (stock: { code: string; name: string }) => void }) {
    const evidence = item.evidence || [];
    return (
        <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
                <button type="button" onClick={() => onOpenStock({ code: item.code, name: item.name || item.code })} className="text-left">
                    <p className="text-base font-black text-slate-900 hover:text-indigo-600">{item.name || item.code}</p>
                    <p className="mt-0.5 text-[10px] font-bold text-slate-400">{item.code} · {item.industry || '未知行业'} · {(item.scopes || []).join(' / ')}</p>
                </button>
                <span className="text-[10px] font-black text-slate-400">{evidence.length} 条</span>
            </div>
            <div className="mt-3 space-y-2">
                {evidence.map((entry, idx) => (
                    <div key={`${entry.title}-${idx}`} className="flex items-start gap-2 border-t border-slate-100 pt-2 first:border-t-0 first:pt-0">
                        <EvidenceIcon kind={entry.kind} />
                        <div className="min-w-0 flex-1">
                            {entry.url ? (
                                <a href={entry.url} target="_blank" rel="noreferrer" className="flex items-start gap-1 text-xs font-black leading-relaxed text-slate-700 hover:text-indigo-600">
                                    <span>{entry.title}</span><ExternalLink size={10} className="mt-1 shrink-0" />
                                </a>
                            ) : <p className="text-xs font-black leading-relaxed text-slate-700">{entry.title}</p>}
                            <p className="mt-0.5 text-[10px] font-bold text-slate-400">{entry.date || '--'} · {entry.source || entry.type || '--'}</p>
                        </div>
                    </div>
                ))}
                {!evidence.length && <p className="py-4 text-center text-xs font-bold text-slate-400">暂无近期公开证据</p>}
            </div>
        </div>
    );
}

function EvidenceIcon({ kind }: { kind?: string }) {
    if (kind === 'RISK') return <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600" />;
    if (kind === 'CATALYST') return <Sparkles size={14} className="mt-0.5 shrink-0 text-rose-600" />;
    return <FileText size={14} className="mt-0.5 shrink-0 text-slate-400" />;
}

function RadarStat({ label, value, tone }: { label: string; value: number; tone?: 'risk' | 'catalyst' }) {
    return (
        <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
            <p className="text-[10px] font-black text-slate-400">{label}</p>
            <p className={cn("mt-1 text-lg font-black", tone === 'risk' ? 'text-amber-700' : tone === 'catalyst' ? 'text-rose-600' : 'text-slate-800')}>{value}</p>
        </div>
    );
}
