"use client";

import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, Download, Loader2, RefreshCw, Target, TrendingUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

export default function ReviewCenter() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [days, setDays] = useState(120);

    const fetchData = async () => {
        setLoading(true);
        try {
            const res = await api.get(`/api/review/scan-performance?days=${days}`);
            setData(res.data);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchData(); }, [days]);

    const exportCsv = () => {
        const baseUrl = api.defaults.baseURL || 'http://127.0.0.1:8000';
        window.open(`${baseUrl}/api/review/scan-performance/export?days=${days}`, '_blank');
    };

    if (loading && !data) {
        return <div className="flex items-center justify-center p-20 text-slate-400"><Loader2 className="animate-spin mr-2" /> 正在计算复盘表现...</div>;
    }

    const summary = data?.summary || {};

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-2xl font-black text-slate-900">交易复盘中心</h2>
                    <p className="text-xs text-slate-400 font-bold uppercase tracking-widest mt-1">Signal performance review</p>
                </div>
                <div className="flex items-center gap-2">
                    {[60, 120, 250].map(v => (
                        <button key={v} onClick={() => setDays(v)} className={cn("px-3 py-2 rounded-xl text-xs font-black", days === v ? "bg-indigo-600 text-white" : "bg-white border border-slate-100 text-slate-500")}>{v}日</button>
                    ))}
                    <button onClick={exportCsv} className="p-2.5 rounded-xl bg-white border border-slate-100 text-slate-600 hover:text-indigo-600" title="导出CSV"><Download size={16} /></button>
                    <button onClick={fetchData} className="p-2.5 rounded-xl bg-white border border-slate-100 text-indigo-600"><RefreshCw size={16} /></button>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <Stat label="有效信号" value={`${summary.signals || 0}`} sub="有未来价格可验证" icon={<Target size={20} />} />
                <Stat label="5日胜率" value={`${summary.win_rate_5d || 0}%`} sub={`均收 ${summary.avg_return_5d >= 0 ? '+' : ''}${summary.avg_return_5d || 0}%`} icon={<TrendingUp size={20} />} hot={(summary.win_rate_5d || 0) >= 50} />
                <Stat label="优势板块" value={summary.best_bucket || "暂无"} sub="按5日胜率排序" icon={<BarChart3 size={20} />} />
                <Stat label="薄弱板块" value={summary.worst_bucket || "暂无"} sub="建议降低权重" icon={<Activity size={20} />} />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <ChartCard title="不同持有周期表现" data={data?.horizons || []} xKey="horizon" barKey="avg_return" />
                <ChartCard title="策略模板表现" data={data?.by_strategy || []} xKey="strategy" barKey="win_rate" suffix="%" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="板块表现 Top" rows={data?.by_industry || []} nameKey="industry" />
                <TableCard title="最近扫描日期表现" rows={data?.recent_dates || []} nameKey="date" />
            </div>
        </div>
    );
}

function Stat({ label, value, sub, icon, hot = false }: { label: string; value: string; sub: string; icon: React.ReactNode; hot?: boolean }) {
    return (
        <div className="glass-card p-6 flex items-center gap-4">
            <div className={cn("w-12 h-12 rounded-2xl flex items-center justify-center", hot ? "bg-rose-50 text-rose-600" : "bg-indigo-50 text-indigo-600")}>{icon}</div>
            <div className="min-w-0">
                <p className="text-[10px] text-slate-400 font-black uppercase tracking-widest">{label}</p>
                <h3 className="text-xl font-black text-slate-900 mt-1 truncate">{value}</h3>
                <p className="text-xs text-slate-400 font-bold mt-1 truncate">{sub}</p>
            </div>
        </div>
    );
}

function ChartCard({ title, data, xKey, barKey, suffix = "%" }: { title: string; data: any[]; xKey: string; barKey: string; suffix?: string }) {
    return (
        <div className="glass-card p-6 h-[340px]">
            <h3 className="font-black text-slate-800 mb-4">{title}</h3>
            <ResponsiveContainer width="100%" height="85%">
                <BarChart data={data}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(148,163,184,0.18)" />
                    <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: '#64748b', fontWeight: 700 }} />
                    <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} />
                    <Tooltip formatter={(v: any) => [`${v}${suffix}`, title]} />
                    <Bar dataKey={barKey} fill="#6366f1" radius={[8, 8, 0, 0]} />
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}

function TableCard({ title, rows, nameKey }: { title: string; rows: any[]; nameKey: string }) {
    return (
        <div className="glass-card p-6">
            <h3 className="font-black text-slate-800 mb-4">{title}</h3>
            <div className="space-y-2">
                {rows.slice(0, 10).map((row, idx) => (
                    <div key={`${row[nameKey]}-${idx}`} className="flex items-center justify-between py-2 border-b border-slate-50 last:border-b-0">
                        <div>
                            <p className="text-sm font-black text-slate-700">{row[nameKey]}</p>
                            <p className="text-[10px] font-bold text-slate-400">{row.signals} 个信号</p>
                        </div>
                        <div className="text-right">
                            <p className="text-sm font-black text-indigo-600">{row.win_rate}%</p>
                            <p className={cn("text-[10px] font-bold", row.avg_return >= 0 ? "text-rose-500" : "text-emerald-500")}>{row.avg_return >= 0 ? '+' : ''}{row.avg_return}%</p>
                        </div>
                    </div>
                ))}
                {rows.length === 0 && <div className="py-12 text-center text-slate-400 font-bold">暂无可复盘数据</div>}
            </div>
        </div>
    );
}
