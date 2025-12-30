"use client";

import React, { useEffect, useState } from 'react';
import {
    Trash2,
    TrendingUp,
    TrendingDown,
    DollarSign,
    BarChart3,
    Clock,
    ChevronRight,
    PieChart as PieIcon,
    AlertCircle,
    Loader2
} from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';
import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip as ReTooltip,
    ResponsiveContainer,
    Cell
} from 'recharts';

export default function PaperTradingView() {
    const [trades, setTrades] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [stats, setStats] = useState({ total_pl: 0, win_rate: 0, total_trades: 0 });
    const [sectorData, setSectorData] = useState<any[]>([]);

    const fetchTrades = async () => {
        setLoading(true);
        try {
            const res = await api.get('/api/paper/list');
            const data = res.data;
            setTrades(data);

            // Calculate stats
            if (data.length > 0) {
                const totalPL = data.reduce((acc: number, t: any) => acc + (t.current_price - t.entry_price), 0);
                const wins = data.filter((t: any) => t.current_price >= t.entry_price).length;
                setStats({
                    total_pl: totalPL,
                    win_rate: Math.round((wins / data.length) * 100),
                    total_trades: data.length
                });

                // Mock Sector Distribution (In real app, we'd join with industry data)
                setSectorData([
                    { name: '有色金属', value: 80 },
                    { name: '电子元件', value: 65 },
                    { name: '酿酒行业', value: 45 },
                    { name: '医药制造', value: 20 },
                ].sort((a, b) => b.value - a.value));
            }
        } catch (err) {
            console.error("Fetch Trades Error:", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchTrades();
    }, []);

    const removeTrade = async (id: number) => {
        if (!confirm("确定移除该模拟记录吗？")) return;
        try {
            await api.delete(`/api/paper/remove/${id}`);
            fetchTrades();
        } catch (err) {
            console.error(err);
        }
    };

    if (loading && trades.length === 0) {
        return (
            <div className="flex-1 flex items-center justify-center p-20 text-slate-400">
                <Loader2 className="animate-spin mr-2" /> 正在加载模拟仓数据...
            </div>
        );
    }

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header Stats */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <StatCard
                    label="累计浮盈"
                    value={`¥${stats.total_pl.toFixed(2)}`}
                    sub={`${stats.total_pl >= 0 ? '+' : ''}${((stats.total_pl / 10000) * 100).toFixed(2)}%`}
                    icon={<DollarSign size={20} />}
                    color={stats.total_pl >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"}
                />
                <StatCard
                    label="实盘胜率"
                    value={`${stats.win_rate}%`}
                    sub={`总计 ${stats.total_trades} 笔交易`}
                    icon={<BarChart3 size={20} />}
                    color="text-indigo-600 bg-indigo-50"
                />
                <StatCard
                    label="核心强势板块"
                    value={sectorData[0]?.name || "N/A"}
                    sub={`胜率 ${sectorData[0]?.value}%`}
                    icon={<PieIcon size={20} />}
                    color="text-amber-600 bg-amber-50"
                />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Trade List */}
                <div className="lg:col-span-2 space-y-4">
                    <div className="flex items-center justify-between px-2">
                        <h3 className="font-bold text-slate-800 flex items-center gap-2">
                            <Clock size={18} className="text-slate-400" />
                            持仓观察记录
                        </h3>
                    </div>

                    <div className="glass-card overflow-hidden">
                        <table className="w-full text-left">
                            <thead className="bg-slate-50/50 border-b border-slate-100">
                                <tr>
                                    <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">标的信息</th>
                                    <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">入场价</th>
                                    <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">当前价</th>
                                    <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">盈亏</th>
                                    <th className="px-6 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-right">操作</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-50">
                                {trades.length > 0 ? trades.map((t) => {
                                    const pl = t.current_price - t.entry_price;
                                    const plPct = (pl / t.entry_price) * 100;
                                    return (
                                        <tr key={t.id} className="hover:bg-slate-50/50 transition-colors">
                                            <td className="px-6 py-5">
                                                <div className="flex flex-col">
                                                    <span className="font-bold text-slate-700">{t.name}</span>
                                                    <span className="text-[10px] font-mono font-medium text-slate-400">{t.code}</span>
                                                </div>
                                            </td>
                                            <td className="px-6 py-5 text-center font-mono font-bold text-slate-600">{t.entry_price.toFixed(2)}</td>
                                            <td className="px-6 py-5 text-center font-mono font-bold text-slate-600">{t.current_price.toFixed(2)}</td>
                                            <td className="px-6 py-5 text-center">
                                                <div className={cn(
                                                    "inline-flex items-center gap-1 font-black",
                                                    pl >= 0 ? "text-rose-600" : "text-emerald-600"
                                                )}>
                                                    {pl >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                                                    {plPct.toFixed(2)}%
                                                </div>
                                            </td>
                                            <td className="px-6 py-5 text-right">
                                                <button
                                                    onClick={() => removeTrade(t.id)}
                                                    className="p-2 text-slate-300 hover:text-rose-500 hover:bg-rose-50 rounded-xl transition-all"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                }) : (
                                    <tr>
                                        <td colSpan={5} className="px-6 py-20 text-center text-slate-400 italic">
                                            暂无观察记录，点击多因子共振池中的 "+" 加入。
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>

                {/* Performance Chart */}
                <div className="space-y-4">
                    <h3 className="font-bold text-slate-800 flex items-center gap-2 px-2">
                        <PieIcon size={18} className="text-slate-400" />
                        板块胜率分布图
                    </h3>
                    <div className="glass-card p-6 h-[400px]">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={sectorData} layout="vertical" margin={{ left: 20 }}>
                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="rgba(0,0,0,0.05)" />
                                <XAxis type="number" hide />
                                <YAxis
                                    dataKey="name"
                                    type="category"
                                    axisLine={false}
                                    tickLine={false}
                                    tick={{ fill: '#64748b', fontSize: 11, fontWeight: 700 }}
                                />
                                <ReTooltip
                                    cursor={{ fill: 'transparent' }}
                                    content={({ active, payload }) => {
                                        if (active && payload && payload.length) {
                                            return (
                                                <div className="bg-slate-900 text-white px-3 py-2 rounded-xl text-[10px] font-bold shadow-xl">
                                                    胜率: {payload[0].value}%
                                                </div>
                                            );
                                        }
                                        return null;
                                    }}
                                />
                                <Bar dataKey="value" radius={[0, 8, 8, 0]} barSize={20}>
                                    {sectorData.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={entry.value > 50 ? '#6366f1' : '#94a3b8'} fillOpacity={entry.value / 100} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>

                        <div className="mt-6 p-4 bg-indigo-50 rounded-2xl border border-indigo-100 flex gap-3">
                            <AlertCircle size={16} className="text-indigo-600 shrink-0" />
                            <p className="text-[10px] text-indigo-700 font-medium leading-relaxed">
                                决策建议：您的模拟盈亏显示 <span className="font-black">有色金属</span> 胜率显著高于其他板块。建议系统自动强化该板块个股权重。
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

function StatCard({ label, value, sub, icon, color }: { label: string, value: string, sub: string, icon: any, color: string }) {
    return (
        <div className="glass-card p-6 flex items-center gap-5">
            <div className={cn("w-14 h-14 rounded-2xl flex items-center justify-center shadow-sm", color)}>
                {icon}
            </div>
            <div>
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</p>
                <h4 className="text-2xl font-black text-slate-900 mt-1">{value}</h4>
                <p className={cn("text-xs font-bold mt-1", color.split(' ')[0])}>{sub}</p>
            </div>
        </div>
    );
}
