"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Clock3, Eye, Inbox, Loader2, RefreshCw, Send, XCircle } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';
import ModalOverlay from './ui/ModalOverlay';

type IntentState = 'ISSUED' | 'SEEN' | 'ACCEPTED' | 'SKIPPED' | 'ORDERED' | 'PARTIAL' | 'FILLED' | 'CANCELLED' | 'EXPIRED';

interface ExecutionIntent {
    intent_id: string;
    signal_date: string;
    issued_at: string;
    valid_until?: string;
    code: string;
    name?: string;
    strategy_type: string;
    instruction: string;
    state: IntentState;
    planned_entry_price?: number;
    stop_price?: number;
    target_price?: number;
    planned_position_pct?: number;
    ordered_shares?: number;
    filled_shares?: number;
    actual_price?: number;
    slippage_pct?: number;
}

interface IntentEvent {
    id: number;
    event_at: string;
    from_state?: string;
    to_state: string;
    actual_price?: number;
    shares?: number;
    note?: string;
}

interface Attribution {
    total_intents: number;
    filled: number;
    skipped: number;
    unresolved: number;
    fill_rate_pct: number;
    avg_entry_slippage_pct?: number;
}

const stateLabel: Record<IntentState, string> = {
    ISSUED: '待查看', SEEN: '已看到', ACCEPTED: '已接受', SKIPPED: '已跳过',
    ORDERED: '已委托', PARTIAL: '部分成交', FILLED: '已成交', CANCELLED: '已取消', EXPIRED: '已过期',
};

const actionMap: Partial<Record<IntentState, Array<[IntentState, string]>>> = {
    ISSUED: [['SEEN', '标记已看到'], ['ACCEPTED', '接受计划'], ['SKIPPED', '跳过']],
    SEEN: [['ACCEPTED', '接受计划'], ['SKIPPED', '跳过']],
    ACCEPTED: [['ORDERED', '记录委托'], ['CANCELLED', '取消']],
    ORDERED: [['PARTIAL', '部分成交'], ['FILLED', '全部成交'], ['CANCELLED', '取消']],
    PARTIAL: [['PARTIAL', '更新成交'], ['FILLED', '全部成交'], ['CANCELLED', '取消剩余']],
};

export default function ExecutionInbox() {
    const [items, setItems] = useState<ExecutionIntent[]>([]);
    const [summary, setSummary] = useState<Attribution | null>(null);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState('ACTIVE');
    const [timeline, setTimeline] = useState<{ intent: ExecutionIntent; events: IntentEvent[] } | null>(null);
    const [transition, setTransition] = useState<{ intent: ExecutionIntent; target: IntentState } | null>(null);
    const [shares, setShares] = useState('');
    const [price, setPrice] = useState('');
    const [note, setNote] = useState('');
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [listRes, summaryRes] = await Promise.all([
                api.get('/api/execution-intents', { params: filter !== 'ACTIVE' && filter !== 'ALL' ? { state: filter } : {} }),
                api.get('/api/execution-intents/summary/attribution', { params: { days: 30 } }),
            ]);
            const rows: ExecutionIntent[] = listRes.data.items || [];
            setItems(filter === 'ACTIVE' ? rows.filter(item => !['SKIPPED', 'FILLED', 'CANCELLED', 'EXPIRED'].includes(item.state)) : rows);
            setSummary(summaryRes.data);
        } finally {
            setLoading(false);
        }
    }, [filter]);

    useEffect(() => { load(); }, [load]);

    const openTimeline = async (intent: ExecutionIntent) => {
        const response = await api.get(`/api/execution-intents/${intent.intent_id}`);
        setTimeline(response.data);
    };

    const chooseAction = (intent: ExecutionIntent, target: IntentState) => {
        if (['ORDERED', 'PARTIAL', 'FILLED'].includes(target)) {
            setShares(String(intent.ordered_shares || intent.filled_shares || ''));
            setPrice(target === 'ORDERED' ? '' : String(intent.actual_price || intent.planned_entry_price || ''));
            setNote('');
            setTransition({ intent, target });
            return;
        }
        submitTransition(intent, target, {});
    };

    const submitTransition = async (intent: ExecutionIntent, target: IntentState, extra: Record<string, unknown>) => {
        setSaving(true);
        try {
            await api.post(`/api/execution-intents/${intent.intent_id}/transition`, { target, ...extra });
            setTransition(null);
            await load();
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-3 duration-300">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex items-center gap-3">
                    <div className="rounded-xl bg-blue-700 p-3 text-white"><Inbox size={22} /></div>
                    <div>
                        <h2 className="text-xl font-black text-slate-900">执行收件箱</h2>
                        <p className="text-xs font-medium text-slate-500">只处理Bark明确标记“可交易”的指令</p>
                        <p className="mt-1 text-[10px] font-bold text-slate-400">实际成交后填写股数和均价，系统才会计入真实操作胜率</p>
                    </div>
                </div>
                <button onClick={load} disabled={loading} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700">
                    <RefreshCw size={15} className={cn(loading && 'animate-spin')} />刷新
                </button>
            </div>

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Metric label="30日指令" value={summary?.total_intents ?? '--'} />
                <Metric label="未决" value={summary?.unresolved ?? '--'} tone="amber" />
                <Metric label="已成交" value={summary?.filled ?? '--'} tone="green" />
                <Metric label="成交率" value={summary ? `${summary.fill_rate_pct}%` : '--'} />
                <Metric label="平均滑点" value={summary?.avg_entry_slippage_pct == null ? '--' : `${summary.avg_entry_slippage_pct > 0 ? '+' : ''}${summary.avg_entry_slippage_pct}%`} tone={Number(summary?.avg_entry_slippage_pct) > 1 ? 'red' : 'green'} />
            </div>

            <div className="flex gap-2 overflow-x-auto rounded-xl border border-slate-200 bg-white p-2">
                {[['ACTIVE', '待处理'], ['ALL', '全部'], ['FILLED', '已成交'], ['SKIPPED', '已跳过'], ['EXPIRED', '已过期']].map(([key, label]) => (
                    <button key={key} onClick={() => setFilter(key)} className={cn('rounded-lg px-4 py-2 text-xs font-bold whitespace-nowrap', filter === key ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-50')}>{label}</button>
                ))}
            </div>

            {loading ? (
                <div className="flex min-h-48 items-center justify-center text-slate-400"><Loader2 className="mr-2 animate-spin" />加载执行意图...</div>
            ) : items.length === 0 ? (
                <div className="glass-card flex min-h-48 flex-col items-center justify-center text-slate-400"><CheckCircle2 size={30} className="mb-2 text-emerald-500" /><p className="font-bold">当前没有待处理指令</p></div>
            ) : (
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                    {items.map(intent => (
                        <div key={intent.intent_id} className="glass-card space-y-4 p-5">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="flex items-center gap-2"><h3 className="font-black text-slate-900">{intent.name || intent.code} <span className="font-mono text-slate-500">{intent.code}</span></h3><StateBadge state={intent.state} /></div>
                                    <p className="mt-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">{intent.strategy_type} · {new Date(intent.issued_at).toLocaleString()}</p>
                                </div>
                                <span className="rounded-md bg-emerald-100 px-2 py-1 text-[10px] font-black text-emerald-700">指令：{intent.instruction}</span>
                            </div>
                            <div className="grid grid-cols-4 gap-2">
                                <Price label="计划价" value={intent.planned_entry_price} />
                                <Price label="止损" value={intent.stop_price} tone="red" />
                                <Price label="目标" value={intent.target_price} tone="green" />
                                <Price label="仓位" value={intent.planned_position_pct} suffix="%" />
                            </div>
                            {(intent.ordered_shares || intent.actual_price) && <p className="text-xs text-slate-500">委托 {intent.ordered_shares || 0}股 · 成交 {intent.filled_shares || 0}股 · 均价 {intent.actual_price?.toFixed(2) || '--'} · 滑点 {intent.slippage_pct == null ? '--' : `${intent.slippage_pct > 0 ? '+' : ''}${intent.slippage_pct.toFixed(2)}%`}</p>}
                            <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3">
                                {(actionMap[intent.state] || []).map(([target, label]) => (
                                    <button key={target} disabled={saving} onClick={() => chooseAction(intent, target)} className={cn('rounded-lg px-3 py-2 text-xs font-bold', target === 'FILLED' ? 'bg-emerald-600 text-white' : target === 'SKIPPED' || target === 'CANCELLED' ? 'bg-rose-50 text-rose-700' : 'bg-blue-50 text-blue-700')}>{label}</button>
                                ))}
                                <button onClick={() => openTimeline(intent)} className="ml-auto flex items-center gap-1 rounded-lg px-3 py-2 text-xs font-bold text-slate-500 hover:bg-slate-50"><Clock3 size={14} />时间线</button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {transition && (
                <ModalOverlay
                    open
                    onClose={() => setTransition(null)}
                    labelledBy="execution-transition-dialog-title"
                    zIndexClass="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
                >
                    <div className="w-full max-w-md space-y-4 rounded-2xl bg-white p-6 shadow-2xl">
                        <h3 id="execution-transition-dialog-title" className="font-black text-slate-900">{stateLabel[transition.target]} · {transition.intent.name || transition.intent.code}</h3>
                        <label className="block text-xs font-bold text-slate-600">{transition.target === 'ORDERED' ? '委托股数' : '累计成交股数'}<input type="number" min="1" value={shares} onChange={event => setShares(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2" /></label>
                        {transition.target !== 'ORDERED' && <label className="block text-xs font-bold text-slate-600">累计成交均价<input type="number" min="0" step="0.01" value={price} onChange={event => setPrice(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2" /></label>}
                        <label className="block text-xs font-bold text-slate-600">备注（可选）<input value={note} onChange={event => setNote(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2" /></label>
                        <div className="flex justify-end gap-2"><button onClick={() => setTransition(null)} className="rounded-lg px-4 py-2 text-xs font-bold text-slate-500">取消</button><button disabled={!shares || (transition.target !== 'ORDERED' && !price) || saving} onClick={() => submitTransition(transition.intent, transition.target, { shares: Number(shares), actual_price: price ? Number(price) : undefined, note })} className="flex items-center gap-2 rounded-lg bg-blue-700 px-4 py-2 text-xs font-bold text-white disabled:opacity-40">{saving ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}确认</button></div>
                    </div>
                </ModalOverlay>
            )}

            {timeline && (
                <ModalOverlay
                    open
                    onClose={() => setTimeline(null)}
                    labelledBy="execution-timeline-dialog-title"
                    zIndexClass="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
                >
                    <div className="max-h-[80vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl">
                        <div className="mb-5 flex items-center justify-between"><h3 id="execution-timeline-dialog-title" className="font-black text-slate-900">执行时间线 · {timeline.intent.code}</h3><button onClick={() => setTimeline(null)}><XCircle className="text-slate-400" /></button></div>
                        <div className="space-y-3">{timeline.events.map(event => <div key={event.id} className="flex gap-3 rounded-xl bg-slate-50 p-3"><Eye size={16} className="mt-0.5 shrink-0 text-blue-600" /><div><p className="text-xs font-black text-slate-700">{event.from_state ? `${stateLabel[event.from_state as IntentState] || event.from_state} → ` : ''}{stateLabel[event.to_state as IntentState] || event.to_state}</p><p className="text-[10px] text-slate-400">{new Date(event.event_at).toLocaleString()}{event.shares ? ` · ${event.shares}股` : ''}{event.actual_price ? ` · ¥${event.actual_price}` : ''}</p>{event.note && <p className="mt-1 text-[11px] text-slate-500">{event.note}</p>}</div></div>)}</div>
                    </div>
                </ModalOverlay>
            )}
        </div>
    );
}

function Metric({ label, value, tone = 'blue' }: { label: string; value: string | number; tone?: string }) {
    const color = tone === 'green' ? 'text-emerald-600' : tone === 'red' ? 'text-rose-600' : tone === 'amber' ? 'text-amber-600' : 'text-blue-700';
    return <div className="glass-card p-4"><p className="text-[10px] font-bold text-slate-400">{label}</p><p className={cn('mt-1 text-xl font-black', color)}>{value}</p></div>;
}

function StateBadge({ state }: { state: IntentState }) {
    const terminal = ['SKIPPED', 'CANCELLED', 'EXPIRED'].includes(state);
    return <span className={cn('rounded-md px-2 py-1 text-[9px] font-black', state === 'FILLED' ? 'bg-emerald-100 text-emerald-700' : terminal ? 'bg-slate-100 text-slate-500' : 'bg-amber-100 text-amber-700')}>{stateLabel[state]}</span>;
}

function Price({ label, value, suffix = '', tone = 'slate' }: { label: string; value?: number; suffix?: string; tone?: string }) {
    return <div className="rounded-lg bg-slate-50 p-2"><p className="text-[9px] font-bold text-slate-400">{label}</p><p className={cn('mt-1 text-xs font-black', tone === 'red' ? 'text-rose-600' : tone === 'green' ? 'text-emerald-600' : 'text-slate-700')}>{value == null ? '--' : `${value.toFixed(2)}${suffix}`}</p></div>;
}
