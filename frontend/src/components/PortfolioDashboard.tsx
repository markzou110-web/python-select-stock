import React, { useEffect, useState } from 'react';
import { 
    LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, 
    Tooltip, ResponsiveContainer, BarChart, Bar, PieChart, Pie, Cell, Legend 
} from 'recharts';
import { TrendingUp, TrendingDown, Shield, Target, PieChart as PieIcon, BarChart2 } from 'lucide-react';
import api from '@/lib/api';

interface RiskMetrics {
    sharpe_ratio: number;
    calmar_ratio: number;
    win_rate: number;
    equity_curve: { date: string, equity: number }[];
    max_consecutive_losses: number;
    avg_win: number;
    avg_loss: number;
    expectancy: number;
}

interface Attribution {
    by_industry: { name: string, total_pnl: number, count: number, win_rate: number }[];
    by_strategy: { name: string, total_pnl: number, count: number, win_rate: number }[];
}

interface PortfolioStats {
    risk_metrics: RiskMetrics;
    attribution: Attribution;
    sector_distribution: { name: string, value: number }[];
}

const COLORS = ['#6366f1', '#f43f5e', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4'];

const PortfolioDashboard: React.FC = () => {
    const [stats, setStats] = useState<PortfolioStats | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchStats = async () => {
            try {
                const res = await api.get('/api/paper/portfolio/stats');
                setStats(res.data);
            } catch (err) {
                console.error("Failed to fetch portfolio stats", err);
            } finally {
                setLoading(false);
            }
        };
        fetchStats();
    }, []);

    if (loading) return <div className="h-64 flex items-center justify-center"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div></div>;
    if (!stats || !stats.risk_metrics.equity_curve) return <div className="p-8 text-center text-gray-500">暂无组合分析数据，请先进行模拟交易</div>;

    const metrics = stats.risk_metrics;

    return (
        <div className="space-y-6">
            {/* KPI Cards */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="bg-white p-4 rounded-xl border border-gray-100 shadow-sm">
                    <div className="flex items-center gap-2 text-gray-500 text-xs mb-1">
                        <Target size={14} className="text-indigo-500" /> 胜率
                    </div>
                    <div className="text-2xl font-bold text-gray-800">{metrics.win_rate}%</div>
                    <div className="text-[10px] text-gray-400 mt-1">期望值: {metrics.expectancy}%</div>
                </div>
                <div className="bg-white p-4 rounded-xl border border-gray-100 shadow-sm">
                    <div className="flex items-center gap-2 text-gray-500 text-xs mb-1">
                        <TrendingUp size={14} className="text-emerald-500" /> 夏普比率
                    </div>
                    <div className="text-2xl font-bold text-gray-800">{metrics.sharpe_ratio}</div>
                    <div className="text-[10px] text-gray-400 mt-1">风险收益比指标</div>
                </div>
                <div className="bg-white p-4 rounded-xl border border-gray-100 shadow-sm">
                    <div className="flex items-center gap-2 text-gray-500 text-xs mb-1">
                        <Shield size={14} className="text-rose-500" /> 卡尔马比率
                    </div>
                    <div className="text-2xl font-bold text-gray-800">{metrics.calmar_ratio}</div>
                    <div className="text-[10px] text-gray-400 mt-1">收益/最大回撤比</div>
                </div>
                <div className="bg-white p-4 rounded-xl border border-gray-100 shadow-sm">
                    <div className="flex items-center gap-2 text-gray-500 text-xs mb-1">
                        <BarChart2 size={14} className="text-orange-500" /> 盈亏比
                    </div>
                    <div className="text-2xl font-bold text-gray-800">
                        {Math.abs(metrics.avg_win / (metrics.avg_loss || 1)).toFixed(2)}
                    </div>
                    <div className="text-[10px] text-gray-400 mt-1">均盈 {metrics.avg_win}% / 均亏 {metrics.avg_loss}%</div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Equity Curve */}
                <div className="lg:col-span-2 bg-white p-5 rounded-xl border border-gray-100 shadow-sm">
                    <h4 className="text-sm font-bold text-gray-800 mb-4 flex items-center gap-2">
                        <TrendingUp size={16} /> 组合净值曲线 (Base 100)
                    </h4>
                    <div className="h-64">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={metrics.equity_curve}>
                                <defs>
                                    <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#6366f1" stopOpacity={0.1}/>
                                        <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f8fafc" />
                                <XAxis dataKey="date" hide />
                                <YAxis domain={['auto', 'auto']} hide />
                                <Tooltip 
                                    contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)' }}
                                />
                                <Area 
                                    type="monotone" 
                                    dataKey="equity" 
                                    stroke="#6366f1" 
                                    fillOpacity={1} 
                                    fill="url(#colorEquity)" 
                                    strokeWidth={3}
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Sector Heatmap / Pie */}
                <div className="bg-white p-5 rounded-xl border border-gray-100 shadow-sm">
                    <h4 className="text-sm font-bold text-gray-800 mb-4 flex items-center gap-2">
                        <PieIcon size={16} /> 当前持仓分布
                    </h4>
                    <div className="h-64">
                        {stats.sector_distribution.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={stats.sector_distribution}
                                        cx="50%"
                                        cy="50%"
                                        innerRadius={60}
                                        outerRadius={80}
                                        paddingAngle={5}
                                        dataKey="value"
                                    >
                                        {stats.sector_distribution.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                        ))}
                                    </Pie>
                                    <Tooltip />
                                    <Legend iconType="circle" wrapperStyle={{ fontSize: '10px' }} />
                                </PieChart>
                            </ResponsiveContainer>
                        ) : (
                            <div className="h-full flex items-center justify-center text-gray-400 text-xs italic">空仓中</div>
                        )}
                    </div>
                </div>
            </div>

            {/* Attribution Analysis */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white p-5 rounded-xl border border-gray-100 shadow-sm">
                    <h4 className="text-sm font-bold text-gray-800 mb-4">行业收益归因 (PnL %)</h4>
                    <div className="h-64">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={stats.attribution.by_industry.slice(0, 8)} layout="vertical">
                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f8fafc" />
                                <XAxis type="number" hide />
                                <YAxis dataKey="name" type="category" width={80} style={{ fontSize: '10px' }} />
                                <Tooltip />
                                <Bar dataKey="total_pnl" name="累计收益" radius={[0, 4, 4, 0]}>
                                    {stats.attribution.by_industry.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={entry.total_pnl >= 0 ? '#10b981' : '#f43f5e'} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                <div className="bg-white p-5 rounded-xl border border-gray-100 shadow-sm">
                    <h4 className="text-sm font-bold text-gray-800 mb-4">策略类型收益归因 (PnL %)</h4>
                    <div className="h-64">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={stats.attribution.by_strategy} layout="vertical">
                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f8fafc" />
                                <XAxis type="number" hide />
                                <YAxis dataKey="name" type="category" width={80} style={{ fontSize: '10px' }} />
                                <Tooltip />
                                <Bar dataKey="total_pnl" name="累计收益" radius={[0, 4, 4, 0]}>
                                    {stats.attribution.by_strategy.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={entry.total_pnl >= 0 ? '#6366f1' : '#f43f5e'} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default PortfolioDashboard;
