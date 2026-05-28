"use client";

import React, { useEffect, useState } from 'react';
import { Archive, Loader2, RefreshCw, Star, Trash2, TrendingDown, TrendingUp } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

export default function WatchlistView() {
    const [items, setItems] = useState<any[]>([]);
    const [stats, setStats] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [status, setStatus] = useState<'WATCHING' | 'ALL'>('WATCHING');

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
                    <button onClick={fetchItems} className="p-2.5 rounded-xl bg-indigo-50 text-indigo-600"><RefreshCw size={16} /></button>
                </div>
            </div>

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
                                            <div>
                                                <p className="font-black text-slate-800">{item.name}</p>
                                                <p className="text-[10px] font-mono font-bold text-slate-400">{item.code} · {item.industry}</p>
                                            </div>
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
                                    </td>
                                    <td className="px-6 py-5">
                                        <p className="text-sm font-bold text-slate-600 line-clamp-2">{item.reason || "未填写"}</p>
                                        {item.invalidation && <p className="text-[10px] font-bold text-slate-400 mt-1">失效条件：{item.invalidation}</p>}
                                    </td>
                                    <td className="px-6 py-5 text-right">
                                        <div className="flex items-center justify-end gap-2">
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

function Summary({ label, value, hot = false }: { label: string; value: React.ReactNode; hot?: boolean }) {
    return (
        <div className="glass-card p-6">
            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{label}</p>
            <h3 className={cn("text-2xl font-black mt-2", hot ? "text-indigo-600" : "text-slate-900")}>{value}</h3>
        </div>
    );
}
