"use client";

import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, CalendarDays, Download, Loader2, RefreshCw, Target, TrendingUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

export default function ReviewCenter() {
    const [data, setData] = useState<any>(null);
    const [followup, setFollowup] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [followupLoading, setFollowupLoading] = useState(false);
    const [days, setDays] = useState(120);
    const [historyDates, setHistoryDates] = useState<string[]>([]);
    const [followupDate, setFollowupDate] = useState('');

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

    useEffect(() => {
        const loadDates = async () => {
            const res = await api.get('/api/scan/dates');
            const dates = Array.isArray(res.data) ? res.data : [];
            setHistoryDates(dates);
            if (!followupDate && dates.length > 0) {
                const today = new Date().toISOString().slice(0, 10);
                setFollowupDate(dates[0] === today && dates.length > 1 ? dates[1] : dates[0]);
            }
        };
        loadDates().catch(() => setHistoryDates([]));
    }, []);

    const fetchFollowup = async (date: string) => {
        if (!date) return;
        setFollowupLoading(true);
        try {
            const res = await api.get(`/api/review/next-day-followup?date=${date}&limit=60`);
            setFollowup(res.data);
        } finally {
            setFollowupLoading(false);
        }
    };

    useEffect(() => { fetchFollowup(followupDate); }, [followupDate]);

    const exportCsv = () => {
        const baseUrl = api.defaults.baseURL || 'http://127.0.0.1:8000';
        window.open(`${baseUrl}/api/review/scan-performance/export?days=${days}`, '_blank');
    };

    const exportFollowupCsv = () => {
        if (!followupDate) return;
        const baseUrl = api.defaults.baseURL || 'http://127.0.0.1:8000';
        window.open(`${baseUrl}/api/review/next-day-followup/export?date=${followupDate}&limit=300`, '_blank');
    };

    if (loading && !data) {
        return <div className="flex items-center justify-center p-20 text-slate-400"><Loader2 className="animate-spin mr-2" /> 正在计算复盘表现...</div>;
    }

    const summary = data?.summary || {};
    const execution = data?.execution_summary || {};
    const portfolio = data?.portfolio_sim || {};
    const dataQuality = data?.data_quality || {};

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

            <div className="glass-card p-5">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-black text-slate-800">执行过滤效果</h3>
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Trade bucket review</span>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                    <MiniStat label="可交易信号" value={`${execution.trade_signals || 0}`} />
                    <MiniStat label="可交易1日胜率" value={`${execution.trade_win_rate_1d || 0}%`} hot={(execution.trade_win_rate_1d || 0) >= 50} />
                    <MiniStat label="可交易1日均收" value={`${(execution.trade_avg_return_1d || 0) >= 0 ? '+' : ''}${execution.trade_avg_return_1d || 0}%`} hot={(execution.trade_avg_return_1d || 0) >= 0} />
                    <MiniStat label="过滤Alpha" value={`${(execution.filter_alpha_1d || 0) >= 0 ? '+' : ''}${execution.filter_alpha_1d || 0}%`} hot={(execution.filter_alpha_1d || 0) >= 0} />
                </div>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <MiniStat label="组合回测交易" value={`${portfolio.trades || 0}`} />
                <MiniStat label="组合胜率" value={`${portfolio.win_rate || 0}%`} hot={(portfolio.win_rate || 0) >= 50} />
                <MiniStat label="均笔收益" value={`${(portfolio.avg_return || 0) >= 0 ? '+' : ''}${portfolio.avg_return || 0}%`} hot={(portfolio.avg_return || 0) >= 0} />
                <MiniStat label="复利估算" value={`${(portfolio.total_compound_return || 0) >= 0 ? '+' : ''}${portfolio.total_compound_return || 0}%`} hot={(portfolio.total_compound_return || 0) >= 0} />
            </div>

            <div className="glass-card p-5">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-black text-slate-800">数据质量过滤</h3>
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Adjustment gap guard</span>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                    <MiniStat label="已剔除异常收益" value={`${dataQuality.excluded_adjustment_gap_returns || 0}`} hot={(dataQuality.excluded_adjustment_gap_returns || 0) > 0} />
                    <div className="lg:col-span-2 rounded-md border border-slate-100 bg-slate-50/70 px-3 py-2">
                        <div className="text-[10px] font-black text-slate-400">过滤规则</div>
                        <div className="mt-1 text-xs font-bold text-slate-600">{dataQuality.rule || '暂无异常收益剔除'}</div>
                    </div>
                </div>
            </div>

            <NextDayFollowupCard
                data={followup}
                dates={historyDates}
                selectedDate={followupDate}
                loading={followupLoading}
                onDateChange={setFollowupDate}
                onRefresh={() => fetchFollowup(followupDate)}
                onExport={exportFollowupCsv}
            />

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <ChartCard title="不同持有周期表现" data={data?.horizons || []} xKey="horizon" barKey="avg_return" />
                <ChartCard title="策略模板表现" data={data?.by_strategy || []} xKey="strategy" barKey="win_rate" suffix="%" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <ChartCard title="Brooks 动作表现" data={data?.by_pa_action || []} xKey="action" barKey="win_rate" suffix="%" />
                <TableCard title="Brooks 形态表现" rows={data?.by_price_action || []} nameKey="setup" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <ChartCard title="H2质量表现" data={data?.by_pa_h2_quality || []} xKey="quality" barKey="win_rate" suffix="%" />
                <TableCard title="量能行为表现" rows={data?.by_pa_volume_pattern || []} nameKey="pattern" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="趋势阶段表现" rows={data?.by_pa_trend_phase || []} nameKey="phase" />
                <TableCard title="周线环境表现" rows={data?.by_pa_weekly_context || []} nameKey="context" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="交易桶表现" rows={data?.by_trade_bucket || []} nameKey="bucket" />
                <TableCard title="市场环境表现" rows={data?.by_market_regime || []} nameKey="regime" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                <TableCard title="板块阶段表现" rows={data?.by_sector_phase || []} nameKey="phase" />
                <TableCard title="板块角色表现" rows={data?.by_sector_role || []} nameKey="role" />
                <TableCard title="板块联动表现" rows={data?.by_sector_alignment || []} nameKey="bucket" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="次日开盘表现" rows={data?.by_next_open_gap || []} nameKey="bucket" />
                <TableCard title="陷阱风险分桶" rows={data?.by_pa_trap_risk || []} nameKey="risk" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="Brooks 独立策略回测" rows={data?.brooks_backtests || []} nameKey="strategy" />
                <TableCard title="板块表现 Top" rows={data?.by_industry || []} nameKey="industry" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <RecommendationEventCard rows={data?.recommendation_events || []} />
                <TableCard title="最近扫描日期表现" rows={data?.recent_dates || []} nameKey="date" />
            </div>
        </div>
    );
}

function RecommendationEventCard({ rows }: { rows: any[] }) {
    return (
        <div className="glass-card p-6">
            <h3 className="font-black text-slate-800 mb-4">推荐事件追踪</h3>
            <div className="space-y-2">
                {rows.slice(0, 8).map((row, idx) => (
                    <div key={`${row.code}-${row.event_date}-${idx}`} className="flex items-center justify-between py-2 border-b border-slate-50 last:border-b-0">
                        <div className="min-w-0">
                            <p className="text-sm font-black text-slate-700 truncate">{row.name || row.code}</p>
                            <p className="text-[10px] font-bold text-slate-400">{String(row.event_date || '').slice(0, 10)} · {row.trade_bucket || 'UNKNOWN'} · {row.strategy_type || '--'}</p>
                        </div>
                        <div className="text-right">
                            <p className={cn("text-sm font-black", Number(row.ret_5d || 0) >= 0 ? "text-rose-600" : "text-emerald-600")}>
                                {row.ret_5d == null ? '--' : `${Number(row.ret_5d) >= 0 ? '+' : ''}${Number(row.ret_5d).toFixed(2)}%`}
                            </p>
                            <p className="text-[10px] font-bold text-slate-400">5日</p>
                        </div>
                    </div>
                ))}
                {rows.length === 0 && <div className="py-12 text-center text-slate-400 font-bold">暂无推荐事件</div>}
            </div>
        </div>
    );
}

function NextDayFollowupCard({
    data,
    dates,
    selectedDate,
    loading,
    onDateChange,
    onRefresh,
    onExport,
}: {
    data: any;
    dates: string[];
    selectedDate: string;
    loading: boolean;
    onDateChange: (date: string) => void;
    onRefresh: () => void;
    onExport: () => void;
}) {
    const items = data?.items || [];
    const summary = data?.summary || {};
    const counts = summary.status_counts || {};
    const statusTone: Record<string, string> = {
        涨停验证: 'bg-rose-50 text-rose-600 border-rose-100',
        大涨验证: 'bg-orange-50 text-orange-600 border-orange-100',
        触发入场线: 'bg-indigo-50 text-indigo-600 border-indigo-100',
        触发观察: 'bg-sky-50 text-sky-600 border-sky-100',
        冲高回落: 'bg-amber-50 text-amber-700 border-amber-100',
        风控触发: 'bg-slate-100 text-slate-700 border-slate-200',
        未触发: 'bg-slate-50 text-slate-500 border-slate-100',
        待跟踪: 'bg-slate-50 text-slate-400 border-slate-100',
    };
    const bucketTone: Record<string, string> = {
        TRADE: 'bg-emerald-50 text-emerald-700 border-emerald-100',
        WATCH: 'bg-sky-50 text-sky-700 border-sky-100',
        BLOCK: 'bg-rose-50 text-rose-700 border-rose-100',
    };

    return (
        <div className="glass-card p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between mb-5">
                <div>
                    <h3 className="font-black text-slate-800 flex items-center gap-2">
                        <CalendarDays size={18} className="text-indigo-600" />
                        次日跟踪
                    </h3>
                    <p className="text-xs font-bold text-slate-400 mt-1">验证上一交易日选股是否触发入场、大涨或冲高回落</p>
                </div>
                <div className="flex items-center gap-2">
                    <select
                        value={selectedDate}
                        onChange={(e) => onDateChange(e.target.value)}
                        className="h-9 rounded-md border border-slate-200 bg-white px-3 text-xs font-bold text-slate-600 outline-none"
                    >
                        {dates.map(date => <option key={date} value={date}>{date}</option>)}
                    </select>
                    <button onClick={onRefresh} className="h-9 w-9 rounded-md border border-slate-100 bg-white text-indigo-600 flex items-center justify-center">
                        {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                    </button>
                    <button onClick={onExport} className="h-9 w-9 rounded-md border border-slate-100 bg-white text-slate-600 hover:text-indigo-600 flex items-center justify-center" title="导出次日跟踪">
                        <Download size={15} />
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
                <MiniStat label="跟踪信号" value={`${summary.signals || 0}`} />
                <MiniStat label="已验证" value={`${summary.tracked || 0}`} />
                <MiniStat label="平均最高涨幅" value={`${summary.avg_max_gain_pct >= 0 ? '+' : ''}${summary.avg_max_gain_pct || 0}%`} hot />
                <MiniStat label="大涨/涨停" value={`${(counts['大涨验证'] || 0) + (counts['涨停验证'] || 0)}`} />
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-left">
                    <thead>
                        <tr className="text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
                            <th className="py-2 pr-3">股票</th>
                            <th className="py-2 pr-3">状态</th>
                            <th className="py-2 pr-3">执行</th>
                            <th className="py-2 pr-3">信号价</th>
                            <th className="py-2 pr-3">入场线</th>
                            <th className="py-2 pr-3">最高涨幅</th>
                            <th className="py-2 pr-3">最新表现</th>
                            <th className="py-2 pr-3">形态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.slice(0, 12).map((item: any) => (
                            <tr key={`${item.code}-${item.signal_date}`} className="border-b border-slate-50 last:border-b-0 text-xs">
                                <td className="py-3 pr-3">
                                    <div className="font-black text-slate-800">{item.name}</div>
                                    <div className="font-mono text-[10px] text-slate-400">{item.code} · {item.industry || '--'}</div>
                                </td>
                                <td className="py-3 pr-3">
                                    <span className={cn("px-2 py-1 rounded-md border text-[10px] font-black", statusTone[item.followup_status] || statusTone['未触发'])}>
                                        {item.followup_status}
                                    </span>
                                </td>
                                <td className="py-3 pr-3 min-w-[160px]">
                                    <div className="font-bold text-slate-600">{item.execution_action || '--'}</div>
                                    <span className={cn("inline-flex mt-1 px-1.5 py-0.5 rounded border text-[10px] font-black", bucketTone[item.trade_bucket] || 'bg-slate-50 text-slate-500 border-slate-100')}>
                                        {item.trade_bucket || 'UNKNOWN'}
                                    </span>
                                </td>
                                <td className="py-3 pr-3 font-bold text-slate-600">{formatPrice(item.signal_price)}</td>
                                <td className="py-3 pr-3 font-bold text-slate-600">{formatPrice(item.entry_line)}</td>
                                <td className={cn("py-3 pr-3 font-black", (item.max_gain_pct || 0) >= 5 ? "text-rose-600" : "text-slate-600")}>
                                    {formatSignedPct(item.max_gain_pct)}
                                </td>
                                <td className={cn("py-3 pr-3 font-black", (item.latest_gain_pct || 0) >= 0 ? "text-rose-500" : "text-emerald-600")}>
                                    {formatSignedPct(item.latest_gain_pct)}
                                </td>
                                <td className="py-3 pr-3 font-bold text-slate-500">{item.setup || '--'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {items.length === 0 && (
                    <div className="py-12 text-center text-slate-400 font-bold">
                        {loading ? '正在加载次日跟踪...' : '暂无次日跟踪数据'}
                    </div>
                )}
            </div>
        </div>
    );
}

function MiniStat({ label, value, hot = false }: { label: string; value: string; hot?: boolean }) {
    return (
        <div className="rounded-md border border-slate-100 bg-slate-50/70 px-3 py-2">
            <div className="text-[10px] font-black text-slate-400">{label}</div>
            <div className={cn("mt-1 text-lg font-black", hot ? "text-rose-600" : "text-slate-800")}>{value}</div>
        </div>
    );
}

function formatPrice(value?: number | null) {
    if (value === undefined || value === null) return '--';
    return Number(value).toFixed(2);
}

function formatSignedPct(value?: number | null) {
    if (value === undefined || value === null) return '--';
    return `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
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
            <div className="h-[270px] min-w-0">
                <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(148,163,184,0.18)" />
                        <XAxis dataKey={xKey} tick={{ fontSize: 11, fill: '#64748b', fontWeight: 700 }} />
                        <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} />
                        <Tooltip formatter={(v: any) => [`${v}${suffix}`, title]} />
                        <Bar dataKey={barKey} fill="#6366f1" radius={[8, 8, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </div>
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
