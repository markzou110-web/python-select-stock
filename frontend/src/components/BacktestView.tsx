"use client";

import React, { useState } from 'react';
import { marketApi } from '@/lib/api';
import { Play, TrendingUp, AlertTriangle, Activity } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';

export default function BacktestView() {
    const [code, setCode] = useState("600519");
    const [loading, setLoading] = useState(false);
    const [results, setResults] = useState<any>(null);
    const [config, setConfig] = useState({
        startDate: "2023-01-01",
        endDate: new Date().toISOString().split('T')[0],
        threshold: 0.12,
        rsi_min: 55
    });

    const runBacktest = async () => {
        setLoading(true);
        try {
            const res = await marketApi.runBacktest({
                code: code,
                start_date: config.startDate,
                end_date: config.endDate,
                strategy_params: {
                    threshold: config.threshold,
                    rsi_min: config.rsi_min,
                    strategy: "Resonance"
                }
            });
            setResults(res.data);
        } catch (e: any) {
            alert("回测失败: " + (e.response?.data?.detail || e.message));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex gap-4 items-end bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                <div>
                    <label className="block text-sm font-bold text-slate-500 mb-1">股票代码</label>
                    <input
                        value={code} onChange={e => setCode(e.target.value)}
                        className="border border-slate-300 rounded-lg px-3 py-2 w-32 font-mono font-bold"
                        placeholder="600519"
                    />
                </div>
                <div>
                    <label className="block text-sm font-bold text-slate-500 mb-1">开始日期</label>
                    <input
                        type="date"
                        value={config.startDate} onChange={e => setConfig({ ...config, startDate: e.target.value })}
                        className="border border-slate-300 rounded-lg px-3 py-2"
                    />
                </div>
                <button
                    onClick={runBacktest} disabled={loading}
                    className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 text-white rounded-xl font-bold hover:bg-indigo-700 disabled:opacity-50"
                >
                    {loading ? "计算中..." : <> <Play size={18} /> 开始回测 </>}
                </button>
            </div>

            {results && (
                <div className="space-y-6 animate-in slide-in-from-bottom-4 duration-500">
                    {/* Stats Cards */}
                    <div className="grid grid-cols-4 gap-4">
                        <StatCard
                            label="总收益率"
                            value={`${results.stats.total_return_pct}%`}
                            color={results.stats.total_return_pct > 0 ? "text-red-500" : "text-green-500"}
                            icon={<TrendingUp />}
                        />
                        <StatCard
                            label="最大回撤"
                            value={`${results.stats.max_drawdown}%`}
                            color="text-slate-700"
                            icon={<AlertTriangle />}
                        />
                        <StatCard
                            label="胜率"
                            value={`${results.stats.win_rate}%`}
                            color="text-indigo-600"
                            icon={<Activity />}
                        />
                        <StatCard
                            label="交易次数"
                            value={results.stats.total_trades}
                            color="text-slate-700"
                        />
                    </div>

                    {/* Equity Curve */}
                    <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-200 h-96">
                        <h3 className="font-bold text-slate-700 mb-4">资金曲线 (Equity Curve)</h3>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={results.equity_curve}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                                <XAxis dataKey="date" hide />
                                <YAxis domain={['auto', 'auto']} />
                                <Tooltip
                                    contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)' }}
                                />
                                <Line type="monotone" dataKey="equity" stroke="#4F46E5" strokeWidth={3} dot={false} />
                            </LineChart>
                        </ResponsiveContainer>
                    </div>

                    {/* Trade Log */}
                    <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                        <table className="w-full text-sm text-left">
                            <thead className="bg-slate-50 text-slate-500 font-bold">
                                <tr>
                                    <th className="p-4">日期</th>
                                    <th className="p-4">动作</th>
                                    <th className="p-4">价格</th>
                                    <th className="p-4">盈亏</th>
                                    <th className="p-4">原因</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {results.trades.map((t: any, i: number) => (
                                    <tr key={i} className="hover:bg-slate-50">
                                        <td className="p-4">{t.date}</td>
                                        <td className={`p-4 font-bold ${t.action === 'BUY' ? 'text-red-500' : 'text-blue-500'}`}>{t.action}</td>
                                        <td className="p-4">{t.price.toFixed(2)}</td>
                                        <td className={`p-4 font-bold ${t.profit > 0 ? 'text-red-500' : t.profit < 0 ? 'text-green-500' : ''}`}>
                                            {t.profit ? t.profit.toFixed(2) : '-'}
                                        </td>
                                        <td className="p-4 text-slate-500">{t.reason}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}

function StatCard({ label, value, color, icon }: any) {
    return (
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-200 flex items-center justify-between">
            <div>
                <p className="text-sm font-bold text-slate-400 uppercase tracking-wider">{label}</p>
                <p className={`text-2xl font-black mt-1 ${color}`}>{value}</p>
            </div>
            {icon && <div className="text-slate-200">{icon}</div>}
        </div>
    )
}
