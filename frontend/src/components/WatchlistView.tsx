"use client";

import React, { useEffect, useState } from 'react';
import { Archive, BellRing, LineChart, Loader2, PlusCircle, RefreshCw, Star, Trash2, TrendingDown, TrendingUp } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface WatchlistViewProps {
    onOpenStock?: (stock: { code: string; name: string }) => void;
}

export default function WatchlistView({ onOpenStock }: WatchlistViewProps) {
    const [items, setItems] = useState<any[]>([]);
    const [stats, setStats] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [checking, setChecking] = useState(false);
    const [notice, setNotice] = useState<string>('');
    const [status, setStatus] = useState<'WATCHING' | 'ALL'>('WATCHING');
    const [transferringId, setTransferringId] = useState<number | null>(null);

    const fetchItems = async () => {
        setLoading(true);
        try {
            const res = await api.get(`/api/watchlist/list?status=${status}`);
            setItems(res.data.items || []);
            setStats(res.data.stats || {});
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchItems(); }, [status]);

    const archive = async (id: number) => {
        await api.post(`/api/watchlist/archive/${id}`);
        fetchItems();
    };

    const remove = async (id: number) => {
        if (!confirm("确定删除该观察记录吗？")) return;
        await api.delete(`/api/watchlist/remove/${id}`);
        fetchItems();
    };

    const transferToPaper = async (item: any, force = false) => {
        setTransferringId(item.id);
        setNotice('');
        try {
            const res = await api.post('/api/paper/add', {
                code: item.code,
                name: item.name,
                price: item.current_price || item.watch_price,
                strategy_type: item.strategy_type || 'squeeze',
                remark: item.reason || '观察池转入拟合实盘',
                force,
                trade_mode: 'SIMULATED',
                entry_source: 'watchlist_current_price',
                entry_signal_date: item.latest_date,
                entry_reason_snapshot: `${item.reason || '观察池转入'} / ${item.pa_trade_action || 'WATCH'} / 观察价 ${item.watch_price}`,
                pa_trade_action: item.pa_trade_action,
                pa_trade_setup: item.pa_trade_setup,
                pa_entry_condition: item.pa_entry_condition,
                pa_invalidation: item.pa_invalidation || item.invalidation,
                pa_risk_pct: item.pa_risk_pct,
            });
            if (res.data?.status === 'warning') {
                if (confirm(res.data.detail || '组合风险预算触发，是否继续转入拟合实盘？')) {
                    await transferToPaper(item, true);
                }
                return;
            }
            if (res.data?.status !== 'success') {
                setNotice(res.data?.detail || '转入拟合实盘失败');
                return;
            }
            await api.post(`/api/watchlist/archive/${item.id}`);
            setNotice(`${item.name} 已转入拟合实盘，观察记录已归档`);
            await fetchItems();
        } catch (err: any) {
            console.error("Transfer watchlist to paper error:", err);
            setNotice(err.response?.data?.detail || '转入拟合实盘失败');
        } finally {
            setTransferringId(null);
        }
    };

    const checkTriggers = async () => {
        setChecking(true);
        setNotice('');
        try {
            const res = await api.post('/api/watchlist/check-triggers?notify=true');
            const count = res.data?.count || 0;
            setNotice(count > 0 ? `发现 ${count} 条触发记录，已尝试推送通知` : '暂无触发记录');
            await fetchItems();
        } finally {
            setChecking(false);
        }
    };

    const refreshDecisions = async () => {
        setChecking(true);
        setNotice('');
        try {
            const res = await api.post('/api/watchlist/refresh-decisions');
            setNotice(`已刷新 ${res.data?.updated || 0} 条观察池决策`);
            await fetchItems();
        } finally {
            setChecking(false);
        }
    };

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-2xl font-black text-slate-900">观察池</h2>
                    <p className="text-xs text-slate-400 font-bold uppercase tracking-widest mt-1">Candidates before position entry</p>
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={() => setStatus(status === 'WATCHING' ? 'ALL' : 'WATCHING')} className="px-4 py-2 rounded-xl bg-white border border-slate-100 text-slate-600 text-xs font-black">
                        {status === 'WATCHING' ? '仅观察中' : '全部记录'}
                    </button>
                    <button onClick={checkTriggers} disabled={checking} className="px-4 py-2 rounded-xl bg-amber-50 text-amber-700 text-xs font-black flex items-center gap-2 disabled:opacity-60">
                        {checking ? <Loader2 size={14} className="animate-spin" /> : <BellRing size={14} />}
                        检查触发
                    </button>
                    <button onClick={refreshDecisions} disabled={checking} className="px-4 py-2 rounded-xl bg-indigo-50 text-indigo-700 text-xs font-black flex items-center gap-2 disabled:opacity-60">
                        {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                        刷新决策
                    </button>
                    <button onClick={fetchItems} className="p-2.5 rounded-xl bg-indigo-50 text-indigo-600"><RefreshCw size={16} /></button>
                </div>
            </div>

            {notice && (
                <div className="px-4 py-3 rounded-xl bg-slate-50 border border-slate-100 text-sm font-bold text-slate-600">
                    {notice}
                </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Summary label="观察标的" value={stats.total || 0} />
                <Summary label="触发条件" value={stats.triggered || 0} hot />
                <Summary label="平均表现" value={`${stats.avg_pl_pct >= 0 ? '+' : ''}${stats.avg_pl_pct || 0}%`} hot={(stats.avg_pl_pct || 0) >= 0} />
            </div>

            <div className="glass-card overflow-hidden">
                {loading ? (
                    <div className="p-20 text-center text-slate-400 font-bold"><Loader2 className="animate-spin inline mr-2" /> 正在刷新观察池...</div>
                ) : (
                    <table className="w-full text-left">
                        <thead className="bg-slate-50/60 border-b border-slate-100">
                            <tr>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">标的</th>
                                <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">观察价/现价</th>
                                <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">目标/失效</th>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">观察理由</th>
                                <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-right">操作</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-50">
                            {items.map(item => (
                                <tr key={item.id} className="hover:bg-slate-50/50 transition-colors">
                                    <td className="px-6 py-5">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center"><Star size={18} /></div>
                                            <button
                                                onClick={() => onOpenStock?.({ code: item.code, name: item.name })}
                                                className="text-left min-w-0 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                                                title="查看K线走势"
                                            >
                                                <p className="font-black text-slate-800 hover:text-indigo-600 transition-colors">{item.name}</p>
                                                <p className="text-[10px] font-mono font-bold text-slate-400">{item.code} · {item.industry}</p>
                                            </button>
                                        </div>
                                    </td>
                                    <td className="px-4 py-5 text-center">
                                        <p className="font-mono font-black text-slate-700">{item.watch_price.toFixed(2)} → {item.current_price.toFixed(2)}</p>
                                        <p className={cn("text-xs font-black mt-1 flex items-center justify-center gap-1", item.pl_pct >= 0 ? "text-rose-600" : "text-emerald-600")}>
                                            {item.pl_pct >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                                            {item.pl_pct >= 0 ? '+' : ''}{item.pl_pct}%
                                        </p>
                                    </td>
                                    <td className="px-4 py-5 text-center">
                                        <p className={cn("text-xs font-black", item.target_hit ? "text-rose-600" : "text-slate-500")}>目标 {item.target_price || '--'}</p>
                                        <p className={cn("text-xs font-black mt-1", item.stop_hit ? "text-emerald-600" : "text-slate-500")}>失效 {item.stop_price || '--'}</p>
                                        {(item.target_hit || item.stop_hit) && (
                                            <span className="inline-flex mt-2 px-2 py-1 rounded-full bg-amber-50 text-[10px] font-black text-amber-700">
                                                已触发
                                            </span>
                                        )}
                                    </td>
                                    <td className="px-6 py-5">
                                        <p className="text-sm font-bold text-slate-600 line-clamp-2">{item.reason || "未填写"}</p>
                                        {item.invalidation && <p className="text-[10px] font-bold text-slate-400 mt-1">失效条件：{item.invalidation}</p>}
                                        <div className="mt-2 rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
                                            <p className={cn(
                                                "text-[10px] font-black",
                                                item.watch_decision === 'PROMOTE' ? "text-emerald-700" :
                                                    item.watch_decision === 'INVALIDATE' ? "text-rose-700" :
                                                        item.watch_decision === 'NEAR_TRIGGER' ? "text-amber-700" :
                                                            "text-slate-600"
                                            )}>
                                                {decisionLabel(item.watch_decision)}
                                            </p>
                                            <p className="mt-1 text-[10px] font-bold text-slate-500">{item.watch_action || item.computed_action || '等待系统刷新'}</p>
                                        </div>
                                        {item.pa_trade_action && (
                                            <div className="mt-2 flex flex-wrap gap-1.5">
                                                <span className={cn(
                                                    "text-[10px] px-2 py-0.5 rounded-full font-black border",
                                                    item.pa_trade_action === 'READY' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                                        item.pa_trade_action === 'AVOID' ? "bg-rose-50 text-rose-700 border-rose-100" :
                                                            "bg-amber-50 text-amber-700 border-amber-100"
                                                )}>
                                                    {item.pa_trade_action}
                                                </span>
                                                {item.pa_trade_setup && (
                                                    <span className="text-[10px] px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full font-bold border border-blue-100">{item.pa_trade_setup}</span>
                                                )}
                                                {item.pa_risk_pct != null && (
                                                    <span className="text-[10px] px-2 py-0.5 bg-slate-50 text-slate-500 rounded-full font-bold border border-slate-100">风险 {item.pa_risk_pct}%</span>
                                                )}
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-6 py-5 text-right">
                                        <div className="flex items-center justify-end gap-2">
                                            <button
                                                onClick={() => onOpenStock?.({ code: item.code, name: item.name })}
                                                className="inline-flex items-center gap-1.5 rounded-xl bg-slate-50 px-3 py-2 text-xs font-black text-slate-600 hover:bg-slate-100"
                                                title="查看K线走势"
                                            >
                                                <LineChart size={14} />
                                                走势
                                            </button>
                                            <button
                                                onClick={() => transferToPaper(item)}
                                                disabled={transferringId === item.id}
                                                className="inline-flex items-center gap-1.5 rounded-xl bg-indigo-50 px-3 py-2 text-xs font-black text-indigo-600 hover:bg-indigo-100 disabled:opacity-60"
                                                title="转入拟合实盘"
                                            >
                                                {transferringId === item.id ? <Loader2 size={14} className="animate-spin" /> : <PlusCircle size={14} />}
                                                拟合实盘
                                            </button>
                                            <button onClick={() => archive(item.id)} className="p-2 text-slate-400 hover:text-amber-600 hover:bg-amber-50 rounded-xl" title="归档"><Archive size={16} /></button>
                                            <button onClick={() => remove(item.id)} className="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-xl" title="删除"><Trash2 size={16} /></button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                            {items.length === 0 && (
                                <tr><td colSpan={5} className="px-6 py-20 text-center text-slate-400 font-bold">暂无观察标的，可从扫描结果或代码检索加入</td></tr>
                            )}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}

function decisionLabel(decision?: string) {
    const labels: Record<string, string> = {
        PROMOTE: '转可交易',
        INVALIDATE: '失效移除',
        NEAR_TRIGGER: '接近触发',
        RISK: '贴近失效',
        WATCH_PULLBACK: '等回踩',
        KEEP_WATCH: '继续观察',
    };
    return labels[decision || ''] || '继续观察';
}

function Summary({ label, value, hot = false }: { label: string; value: React.ReactNode; hot?: boolean }) {
    return (
        <div className="glass-card p-6">
            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{label}</p>
            <h3 className={cn("text-2xl font-black mt-2", hot ? "text-indigo-600" : "text-slate-900")}>{value}</h3>
        </div>
    );
}
