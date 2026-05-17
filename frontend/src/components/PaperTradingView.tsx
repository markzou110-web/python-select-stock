"use client";

import React, { useEffect, useState, useMemo } from 'react';
import {
    Trash2,
    TrendingUp,
    TrendingDown,
    DollarSign,
    BarChart3,
    Clock,
    PieChart as PieIcon,
    AlertCircle,
    Loader2,
    RefreshCw,
    CalendarDays,
    Trophy,
    Skull,
    LogOut,
    CheckCircle2
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
    Cell,
    AreaChart,
    Area
} from 'recharts';
import { useScanStore } from '@/stores/scanStore';
import PortfolioDashboard from './PortfolioDashboard';

interface Trade {
    id: number;
    code: string;
    name: string;
    entry_price: number;
    current_price: number;
    entry_date: string;
    pl: number;
    pl_pct: number;
    hold_days: number;
    industry: string;
    status: string;
    close_price?: number;
    close_date?: string;
    remark?: string;
    high_since_entry?: number;
}

interface Stats {
    total_trades: number;
    wins: number;
    losses: number;
    flat: number;
    win_rate: number;
    avg_pl_pct: number;
    total_pl_pct: number;
    avg_hold_days: number;
    max_drawdown?: number;
    profit_factor?: number;
    best_trade?: { name: string; pl_pct: number } | null;
    worst_trade?: { name: string; pl_pct: number } | null;
    sector_distribution?: { name: string; value: number; count: number }[];
    monte_carlo?: {
        distribution: { range: string; count: number; value: number }[];
        p90: number; p50: number; p10: number; max: number; min: number;
    };
    rolling_performance?: { date: string; avg_return: number; win_rate: number }[];
    risk_metrics?: {
        sharpe_ratio: number;
        calmar_ratio: number;
        equity_curve: { date: string; equity: number }[];
        max_consecutive_losses: number;
        avg_win: number;
        avg_loss: number;
        expectancy: number;
    };
    pnl_attribution?: {
        by_industry: { name: string; total_pnl: number; count: number; avg_pnl: number; win_rate: number }[];
        by_strategy: { name: string; total_pnl: number; count: number; avg_pnl: number; win_rate: number }[];
    };
}

export default function PaperTradingView() {
    const [trades, setTrades] = useState<Trade[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [tab, setTab] = useState<'open' | 'closed' | 'analytics'>('open');
    const [closingId, setClosingId] = useState<number | null>(null);
    const [closePrice, setClosePrice] = useState('');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [stats, setStats] = useState<Stats>({
        total_trades: 0, wins: 0, losses: 0, flat: 0,
        win_rate: 0, avg_pl_pct: 0, total_pl_pct: 0, avg_hold_days: 0,
        rolling_performance: []
    });
    const setSelectedStock = useScanStore(s => s.setSelectedStock);

    const fetchTrades = async (showRefresh = false) => {
        if (showRefresh) setRefreshing(true); else setLoading(true);
        try {
            const res = await api.get('/api/paper/list');
            const data = res.data;
            setTrades(data.trades || []);
            if (data.stats) setStats(data.stats);
        } catch (err) {
            console.error("Fetch Trades Error:", err);
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    useEffect(() => { fetchTrades(); }, []);

    const removeTrade = async (id: number) => {
        if (!confirm("确定移除该记录吗？（数据将被删除）")) return;
        try {
            await api.delete(`/api/paper/remove/${id}`);
            fetchTrades();
        } catch (err) { console.error(err); }
    };

    const closeTrade = async (id: number) => {
        const price = parseFloat(closePrice);
        if (isNaN(price) || price <= 0) {
            setToast({ message: '请输入有效的卖出价格', type: 'error' });
            setTimeout(() => setToast(null), 2500);
            return;
        }
        try {
            await api.post(`/api/paper/close/${id}`, { close_price: price });
            setClosingId(null);
            setClosePrice('');
            fetchTrades();
        } catch (err) { console.error(err); }
    };

    const openTrades = useMemo(() => trades.filter(t => t.status === 'OPEN'), [trades]);
    const closedTrades = useMemo(() => trades.filter(t => t.status === 'CLOSED'), [trades]);
    const displayTrades = tab === 'open' ? openTrades : (tab === 'closed' ? closedTrades : []);

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
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
                <StatCard
                    label="最大回撤"
                    value={`${stats.max_drawdown}%`}
                    sub={`盈亏比 ${stats.profit_factor}`}
                    icon={<AlertCircle size={20} />}
                    color="text-slate-600 bg-slate-50"
                />
                <StatCard
                    label="胜率 / 盈亏"
                    value={`${stats.win_rate}%`}
                    sub={`${stats.wins}胜 / ${stats.losses}负`}
                    icon={<BarChart3 size={20} />}
                    color={stats.win_rate >= 50 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"}
                />
                <StatCard
                    label="平均收益/累计"
                    value={`${stats.avg_pl_pct >= 0 ? '+' : ''}${stats.avg_pl_pct}%`}
                    sub={`累计 ${stats.total_pl_pct >= 0 ? '+' : ''}${stats.total_pl_pct}%`}
                    icon={<DollarSign size={20} />}
                    color={stats.avg_pl_pct >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"}
                />
                <StatCard
                    label="平均持仓/核心板块"
                    value={`${stats.avg_hold_days}天`}
                    sub={stats.sector_distribution?.[0]?.name || "N/A"}
                    icon={<PieIcon size={20} />}
                    color="text-indigo-600 bg-indigo-50"
                />
            </div>

            {/* Sharpe / Expectancy Cards */}
            {stats.risk_metrics && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div className={cn(
                        "glass-card px-5 py-4 flex items-center justify-between",
                        stats.risk_metrics.sharpe_ratio >= 1.5 ? "ring-1 ring-rose-200" :
                        stats.risk_metrics.sharpe_ratio >= 0.5 ? "ring-1 ring-amber-200" : ""
                    )}>
                        <div>
                            <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest">夏普比率</p>
                            <p className={cn(
                                "text-xl font-black mt-0.5",
                                stats.risk_metrics.sharpe_ratio >= 1.5 ? "text-rose-600" :
                                stats.risk_metrics.sharpe_ratio >= 0.5 ? "text-amber-600" : "text-slate-500"
                            )}>{stats.risk_metrics.sharpe_ratio}</p>
                        </div>
                        <div className={cn(
                            "text-[9px] font-black px-2 py-1 rounded-lg",
                            stats.risk_metrics.sharpe_ratio >= 1.5 ? "bg-rose-50 text-rose-600" :
                            stats.risk_metrics.sharpe_ratio >= 0.5 ? "bg-amber-50 text-amber-600" : "bg-slate-100 text-slate-500"
                        )}>
                            {stats.risk_metrics.sharpe_ratio >= 1.5 ? '⭐ 优秀' :
                             stats.risk_metrics.sharpe_ratio >= 0.5 ? '⚠️ 一般' : '❌ 偏低'}
                        </div>
                    </div>
                    <div className="glass-card px-5 py-4">
                        <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest">期望值</p>
                        <p className={cn(
                            "text-xl font-black mt-0.5",
                            stats.risk_metrics.expectancy > 0 ? "text-rose-600" : "text-emerald-600"
                        )}>{stats.risk_metrics.expectancy > 0 ? '+' : ''}{stats.risk_metrics.expectancy}%</p>
                    </div>
                    <div className="glass-card px-5 py-4">
                        <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest">平均盈/亏</p>
                        <div className="flex items-center gap-1.5 mt-1">
                            <span className="text-sm font-black text-rose-500">+{stats.risk_metrics.avg_win}%</span>
                            <span className="text-slate-300">/</span>
                            <span className="text-sm font-black text-emerald-500">{stats.risk_metrics.avg_loss}%</span>
                        </div>
                    </div>
                    <div className="glass-card px-5 py-4">
                        <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest">最大连亏</p>
                        <p className="text-xl font-black text-slate-700 mt-0.5">
                            {stats.risk_metrics.max_consecutive_losses}
                            <span className="text-xs font-bold text-slate-400 ml-1">笔</span>
                        </p>
                    </div>
                </div>
            )}

            {/* Best / Worst highlight */}
            {(stats.best_trade || stats.worst_trade) && (
                <div className="grid grid-cols-2 gap-4">
                    {stats.best_trade && (
                        <div className="flex items-center gap-3 px-5 py-3 bg-rose-50 border border-rose-100 rounded-2xl">
                            <Trophy size={18} className="text-rose-500" />
                            <div>
                                <p className="text-[10px] font-bold text-rose-400 uppercase tracking-widest">最佳单笔</p>
                                <p className="text-sm font-black text-rose-700">{stats.best_trade.name} <span className="text-rose-500">+{stats.best_trade.pl_pct}%</span></p>
                            </div>
                        </div>
                    )}
                    {stats.worst_trade && stats.worst_trade.pl_pct < 0 && (
                        <div className="flex items-center gap-3 px-5 py-3 bg-emerald-50 border border-emerald-100 rounded-2xl">
                            <Skull size={18} className="text-emerald-500" />
                            <div>
                                <p className="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">最差单笔</p>
                                <p className="text-sm font-black text-emerald-700">{stats.worst_trade.name} <span className="text-emerald-500">{stats.worst_trade.pl_pct}%</span></p>
                            </div>
                        </div>
                    )}
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Trade List or Analytics */}
                <div className="lg:col-span-2 space-y-4">
                    <div className="flex items-center justify-between px-2">
                        <div className="flex items-center gap-4">
                            <h3 className="font-bold text-slate-800 flex items-center gap-2">
                                <Clock size={18} className="text-slate-400" />
                                {tab === 'analytics' ? '分析报告' : '交易记录'}
                            </h3>
                            {/* Tabs */}
                            <div className="flex bg-slate-100 rounded-xl p-1 gap-0.5">
                                <button
                                    onClick={() => setTab('open')}
                                    className={cn(
                                        "px-3 py-1 text-[10px] font-black uppercase tracking-widest rounded-lg transition-all",
                                        tab === 'open' ? "bg-white text-indigo-600 shadow-sm" : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    持仓中 ({openTrades.length})
                                </button>
                                <button
                                    onClick={() => setTab('closed')}
                                    className={cn(
                                        "px-3 py-1 text-[10px] font-black uppercase tracking-widest rounded-lg transition-all",
                                        tab === 'closed' ? "bg-white text-emerald-600 shadow-sm" : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    已平仓 ({closedTrades.length})
                                </button>
                                <button
                                    onClick={() => setTab('analytics')}
                                    className={cn(
                                        "px-3 py-1 text-[10px] font-black uppercase tracking-widest rounded-lg transition-all",
                                        tab === 'analytics' ? "bg-white text-indigo-600 shadow-sm" : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    深度分析
                                </button>
                            </div>
                        </div>
                        {tab !== 'analytics' && (
                            <button
                                onClick={() => fetchTrades(true)}
                                disabled={refreshing}
                                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-xl transition-all disabled:opacity-50"
                            >
                                <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
                                刷新价格
                            </button>
                        )}
                    </div>

                    {tab === 'analytics' ? (
                        <div className="space-y-8 animate-in fade-in duration-500">
                            <PortfolioDashboard />
                        </div>
                    ) : (
                        <div className="glass-card overflow-hidden">
                            <table className="w-full text-left">
                                <thead className="bg-slate-50/50 border-b border-slate-100">
                                    <tr>
                                        <th className="px-5 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">标的信息</th>
                                        <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">入场价</th>
                                        <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">
                                            {tab === 'closed' ? '卖出价' : '当前价'}
                                        </th>
                                        <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">盈亏</th>
                                        <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">天数</th>
                                        <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-center">板块</th>
                                        <th className="px-4 py-4 text-[10px] font-black text-slate-400 uppercase tracking-widest text-right">操作</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-50">
                                    {displayTrades.length > 0 ? displayTrades.map((t) => {
                                        const showPrice = t.status === 'CLOSED' && t.close_price ? t.close_price : t.current_price;
                                        const pl_pct = t.status === 'CLOSED' && t.close_price
                                            ? ((t.close_price - t.entry_price) / t.entry_price * 100)
                                            : t.pl_pct;
                                        return (
                                            <tr key={t.id} className="hover:bg-slate-50/50 transition-colors">
                                                <td className="px-5 py-4">
                                                    <div 
                                                        className="flex flex-col cursor-pointer group"
                                                        onClick={() => setSelectedStock({ 代码: t.code, 名称: t.name } as any)}
                                                    >
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-bold text-slate-700 group-hover:text-indigo-600 transition-colors">{t.name}</span>
                                                            {t.status === 'CLOSED' && (
                                                                <span className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 text-[9px] font-black rounded border border-emerald-100">
                                                                    已平仓
                                                                </span>
                                                            )}
                                                        </div>
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-[10px] font-mono font-medium text-slate-400 group-hover:text-indigo-400 transition-colors">{t.code}</span>
                                                            <span className="text-[9px] font-bold text-slate-300">{t.entry_date}</span>
                                                            {t.close_date && <span className="text-[9px] font-bold text-emerald-400">→ {t.close_date}</span>}
                                                        </div>
                                                        {t.remark && (
                                                            <span className="text-[10px] text-slate-400 italic leading-relaxed line-clamp-1" title={t.remark}>
                                                                💬 {t.remark}
                                                            </span>
                                                        )}
                                                    </div>
                                                </td>
                                                <td className="px-4 py-4 text-center font-mono font-bold text-slate-600 text-sm">{t.entry_price.toFixed(2)}</td>
                                                <td className="px-4 py-4 text-center">
                                                    <div className="flex flex-col items-center">
                                                        <span className={cn(
                                                            "font-mono font-bold text-sm",
                                                            showPrice > t.entry_price ? "text-rose-600" :
                                                            showPrice < t.entry_price ? "text-emerald-600" : "text-slate-600"
                                                        )}>
                                                            {showPrice.toFixed(2)}
                                                        </span>
                                                        {t.status === 'OPEN' && t.high_since_entry && (
                                                            <span className="text-[9px] text-slate-400 font-bold mt-0.5">
                                                                峰值: {t.high_since_entry.toFixed(2)}
                                                            </span>
                                                        )}
                                                    </div>
                                                </td>
                                                <td className="px-4 py-4 text-center">
                                                    <div className={cn(
                                                        "inline-flex items-center gap-1 font-black text-sm",
                                                        pl_pct > 0 ? "text-rose-600" :
                                                        pl_pct < 0 ? "text-emerald-600" : "text-slate-400"
                                                    )}>
                                                        {pl_pct > 0 ? <TrendingUp size={13} /> :
                                                        pl_pct < 0 ? <TrendingDown size={13} /> : null}
                                                        {pl_pct > 0 ? '+' : ''}{pl_pct.toFixed(2)}%
                                                    </div>
                                                </td>
                                                <td className="px-4 py-4 text-center">
                                                    <span className="text-xs font-bold text-slate-500">{t.hold_days}天</span>
                                                </td>
                                                <td className="px-4 py-4 text-center">
                                                    <span className="px-2 py-0.5 bg-indigo-50 text-indigo-600 text-[10px] font-black rounded-lg border border-indigo-100">
                                                        {t.industry}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-4 text-right">
                                                    {t.status === 'OPEN' ? (
                                                        <div className="flex items-center justify-end gap-1">
                                                            {closingId === t.id ? (
                                                                <div className="flex items-center gap-1">
                                                                    <input
                                                                        type="number"
                                                                        step="0.01"
                                                                        placeholder="卖出价"
                                                                        value={closePrice}
                                                                        onChange={e => setClosePrice(e.target.value)}
                                                                        className="w-20 px-2 py-1 text-xs font-mono border border-indigo-200 rounded-lg outline-none focus:ring-2 focus:ring-indigo-400"
                                                                        autoFocus
                                                                        onKeyDown={e => { if (e.key === 'Enter') closeTrade(t.id); if (e.key === 'Escape') { setClosingId(null); setClosePrice(''); } }}
                                                                    />
                                                                    <button
                                                                        onClick={() => closeTrade(t.id)}
                                                                        className="p-1.5 text-emerald-500 hover:bg-emerald-50 rounded-lg transition-all"
                                                                        title="确认平仓"
                                                                    >
                                                                        <CheckCircle2 size={16} />
                                                                    </button>
                                                                </div>
                                                            ) : (
                                                                <>
                                                                    <button
                                                                        onClick={() => { setClosingId(t.id); setClosePrice(t.current_price.toFixed(2)); }}
                                                                        className="p-1.5 text-indigo-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-all"
                                                                        title="平仓卖出"
                                                                    >
                                                                        <LogOut size={15} />
                                                                    </button>
                                                                    <button
                                                                        onClick={() => removeTrade(t.id)}
                                                                        className="p-1.5 text-slate-300 hover:text-rose-500 hover:bg-rose-50 rounded-lg transition-all"
                                                                        title="删除记录"
                                                                    >
                                                                        <Trash2 size={15} />
                                                                    </button>
                                                                </>
                                                            )}
                                                        </div>
                                                    ) : (
                                                        <button
                                                            onClick={() => removeTrade(t.id)}
                                                            className="p-1.5 text-slate-300 hover:text-rose-500 hover:bg-rose-50 rounded-lg transition-all"
                                                            title="删除记录"
                                                        >
                                                            <Trash2 size={15} />
                                                        </button>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    }) : (
                                        <tr>
                                            <td colSpan={7} className="px-6 py-20 text-center text-slate-400 italic">
                                                {tab === 'open' ? '暂无持仓中的记录' : '暂无交易记录'}
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>

                {/* Sidebar charts */}
                <div className="space-y-4">
                    <h3 className="font-bold text-slate-800 flex items-center gap-2 px-2">
                        <PieIcon size={18} className="text-slate-400" />
                        板块胜率分布图
                    </h3>
                    <div className="glass-card p-6 h-[400px]">
                        {stats.sector_distribution && stats.sector_distribution.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={stats.sector_distribution} layout="vertical" margin={{ left: 20 }}>
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
                                        {stats.sector_distribution.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={entry.value > 50 ? '#6366f1' : '#94a3b8'} fillOpacity={Math.max(entry.value / 100, 0.3)} />
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        ) : (
                            <div className="flex items-center justify-center h-full text-slate-400 text-sm">
                                暂无板块数据
                            </div>
                        )}
                        
                        {stats.sector_distribution && stats.sector_distribution.length > 0 && (
                            <div className="mt-6 p-4 bg-indigo-50 rounded-2xl border border-indigo-100 flex gap-3">
                                <AlertCircle size={16} className="text-indigo-600 shrink-0" />
                                <p className="text-[10px] text-indigo-700 font-medium leading-relaxed">
                                    决策建议：您的模拟盈亏显示 <span className="font-black">{stats.sector_distribution[0]?.name}</span> 胜率
                                    {stats.sector_distribution[0]?.value >= 50
                                        ? "显著高于其他板块。建议强化该板块权重。"
                                        : "尚未达优势水平，建议持续积累样本。"}
                                </p>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

function StatCard({ label, value, sub, icon, color }: { label: string, value: string, sub: string, icon: React.ReactNode, color: string }) {
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
