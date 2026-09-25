"use client";

import React, { useState } from 'react';
import { BarChart3, Loader2, Play, TrendingUp } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface BacktestTrade {
    signal_date?: string;
    entry_date: string;
    exit_date: string;
    entry_price: number;
    exit_price: number;
    return_pct: number;
    hold_days: number;
    exit_reason: string;
    signal_reason: string;
    net_pnl: number;
    entry_mode?: string;
    open_gap_pct?: number | null;
    add_date?: string | null;
    add_price?: number | null;
}

interface BacktestResult {
    summary: {
        signal_count: number;
        win_rate: number;
        avg_return: number;
        max_drawdown: number;
        profit_factor: number;
        avg_hold_days: number;
        total_return: number;
        final_equity: number;
        stop_loss_hits?: number;
        time_stopped?: number;
        skipped_high_open?: number;
        skipped_limit_up?: number;
        entry_mode?: string;
        position_mode?: string;
        profit_exit_mode?: string;
        added_position_count?: number;
    };
    trades: BacktestTrade[];
    equity_curve: Array<{ date: string; equity: number }>;
    reason?: string;
    meta?: {
        code: string;
        strategy_type: string;
        start_date: string;
        end_date?: string | null;
        data_points: number;
    };
    versions?: Record<string, string>;
}

interface RollingResult {
    meta: { train_size: number; validation_size: number; test_size: number; params_frozen: boolean };
    summary: {
        test_windows: number;
        test_signals: number;
        oos_weighted_win_rate: number;
        positive_test_windows: number;
        positive_window_ratio: number;
    };
}

export default function BacktestLab() {
    const [code, setCode] = useState('000001');
    const [strategyType, setStrategyType] = useState('pine');
    const [days, setDays] = useState(720);
    const [stopLossPct, setStopLossPct] = useState(-8);
    const [maxHoldDays, setMaxHoldDays] = useState(10);
    const [pineMinSignals, setPineMinSignals] = useState(3);
    const [entryMode, setEntryMode] = useState('next_open_confirm');
    const [maxOpenGapPct, setMaxOpenGapPct] = useState(3);
    const [positionMode, setPositionMode] = useState('single');
    const [profitExitMode, setProfitExitMode] = useState('atr');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<BacktestResult | null>(null);
    const [rollingResult, setRollingResult] = useState<RollingResult | null>(null);

    const runBacktest = async () => {
        setLoading(true);
        setError(null);
        setResult(null);
        try {
            const res = await api.post<BacktestResult>('/api/backtest/single', {
                code,
                strategy_type: strategyType,
                days,
                stop_loss_pct: stopLossPct,
                max_hold_days: maxHoldDays,
                pine_min_signals: pineMinSignals,
                entry_mode: entryMode,
                max_open_gap_pct: maxOpenGapPct,
                position_mode: positionMode,
                profit_exit_mode: profitExitMode,
            }, { timeout: 20000 });
            setResult(res.data);
        } catch (err) {
            const message = err instanceof Error ? err.message : '回测失败';
            setError(message);
        } finally {
            setLoading(false);
        }
    };

    const runRollingBacktest = async () => {
        setLoading(true);
        setError(null);
        setRollingResult(null);
        try {
            const res = await api.post<RollingResult>('/api/backtest/rolling-walk-forward', {
                codes: [code], strategy_type: strategyType, days,
                stop_loss_pct: stopLossPct, max_hold_days: maxHoldDays,
                pine_min_signals: pineMinSignals, entry_mode: entryMode,
                max_open_gap_pct: maxOpenGapPct,
                position_mode: positionMode, profit_exit_mode: profitExitMode,
            }, { timeout: 60000 });
            setRollingResult(res.data);
        } catch (err) {
            setError(err instanceof Error ? err.message : '滚动样本外验证失败');
        } finally {
            setLoading(false);
        }
    };

    const maxEquity = Math.max(...(result?.equity_curve || []).map(item => item.equity), result?.summary.final_equity || 100000, 100000);
    const minEquity = Math.min(...(result?.equity_curve || []).map(item => item.equity), 100000);

    return (
        <div className="space-y-5 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="workspace-panel px-5 py-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="w-11 h-11 rounded-xl bg-slate-900 text-white flex items-center justify-center">
                        <BarChart3 size={22} />
                    </div>
                    <div>
                        <h2 className="text-lg font-black text-slate-900">策略回测</h2>
                        <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Single Stock Backtest Lab</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={runRollingBacktest} disabled={loading} className="toolbar-button">
                        {loading ? <Loader2 size={16} className="animate-spin" /> : <BarChart3 size={16} />}
                        滚动 OOS
                    </button>
                    <button onClick={runBacktest} disabled={loading} className="toolbar-button">
                        {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                        执行回测
                    </button>
                </div>
            </div>

            <section className="glass-card p-5">
                <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-10 gap-3">
                    <Field label="股票代码">
                        <input value={code} onChange={event => setCode(event.target.value)} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20" />
                    </Field>
                    <Field label="策略">
                        <select value={strategyType} onChange={event => setStrategyType(event.target.value)} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20">
                            <option value="pine">Pine 多指标</option>
                            <option value="squeeze">均线粘合</option>
                            <option value="consensus">Azul 共识</option>
                            <option value="high_tight_flag">HTF 高位收敛（SHADOW）</option>
                            <option value="turtle_breakout">20日新高基准（SHADOW）</option>
                            <option value="limit_up_shakeout">涨停洗盘观察（SHADOW）</option>
                        </select>
                    </Field>
                    <Field label="回看天数">
                        <input type="number" value={days} onChange={event => setDays(Number(event.target.value))} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20" />
                    </Field>
                    <Field label="止损%">
                        <input type="number" value={stopLossPct} onChange={event => setStopLossPct(Number(event.target.value))} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20" />
                    </Field>
                    <Field label="最大持有">
                        <input type="number" value={maxHoldDays} onChange={event => setMaxHoldDays(Number(event.target.value))} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20" />
                    </Field>
                    <Field label="Pine信号数">
                        <input type="number" min={1} max={5} value={pineMinSignals} onChange={event => setPineMinSignals(Number(event.target.value))} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20" />
                    </Field>
                    <Field label="成交模式">
                        <select value={entryMode} onChange={event => setEntryMode(event.target.value)} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20">
                            <option value="next_open_confirm">次日开盘确认</option>
                            <option value="signal_close">信号收盘成交</option>
                        </select>
                    </Field>
                    <Field label="高开过滤%">
                        <input type="number" value={maxOpenGapPct} onChange={event => setMaxOpenGapPct(Number(event.target.value))} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20" />
                    </Field>
                    <Field label="仓位方案">
                        <select value={positionMode} onChange={event => setPositionMode(event.target.value)} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20">
                            <option value="single">现行单次仓位</option>
                            <option value="two_stage_50_50">50%试仓＋50%确认</option>
                        </select>
                    </Field>
                    <Field label="止盈方案">
                        <select value={profitExitMode} onChange={event => setProfitExitMode(event.target.value)} className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700 outline-none focus:bg-white focus:ring-2 focus:ring-blue-500/20">
                            <option value="atr">现行ATR移动止盈</option>
                            <option value="half_peak_giveback">峰值利润回撤一半</option>
                        </select>
                    </Field>
                </div>
                {error && <div className="mt-4 rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-700">{error}</div>}
            </section>

            {rollingResult && (
                <section className="glass-card p-5 space-y-4">
                    <SectionTitle title="滚动样本外验证" />
                    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
                        <Tiny label="测试窗口" value={rollingResult.summary.test_windows} />
                        <Tiny label="OOS 成交" value={rollingResult.summary.test_signals} />
                        <Tiny label="加权胜率" value={`${rollingResult.summary.oos_weighted_win_rate}%`} />
                        <Tiny label="正收益窗口" value={`${rollingResult.summary.positive_test_windows}/${rollingResult.summary.test_windows}`} />
                        <Tiny label="正窗口比例" value={`${Math.round(rollingResult.summary.positive_window_ratio * 100)}%`} />
                    </div>
                    <p className="text-xs font-bold text-slate-500">
                        参数已冻结；窗口为 {rollingResult.meta.train_size}/{rollingResult.meta.validation_size}/{rollingResult.meta.test_size} 个交易日。
                        该报告用于研究晋级，不会自动修改正式策略。
                    </p>
                </section>
            )}

            {result && (
                <>
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                        <Metric label="交易次数" value={result.summary.signal_count} />
                        <Metric label="胜率" value={`${result.summary.win_rate}%`} tone={result.summary.win_rate >= 50 ? 'ok' : 'warn'} />
                        <Metric label="总收益" value={`${result.summary.total_return}%`} tone={result.summary.total_return >= 0 ? 'ok' : 'error'} />
                        <Metric label="最大回撤" value={`${result.summary.max_drawdown}%`} tone={result.summary.max_drawdown <= -10 ? 'error' : 'ok'} />
                    </div>

                    <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
                        <section className="glass-card p-5 space-y-4">
                            <SectionTitle title="回测摘要" />
                            <div className="grid grid-cols-2 gap-3">
                                <Tiny label="均笔收益" value={`${result.summary.avg_return}%`} />
                                <Tiny label="盈亏比" value={result.summary.profit_factor} />
                                <Tiny label="均持有" value={`${result.summary.avg_hold_days}天`} />
                                <Tiny label="最终权益" value={Math.round(result.summary.final_equity)} />
                                <Tiny label="高开跳过" value={result.summary.skipped_high_open || 0} />
                                <Tiny label="涨停跳过" value={result.summary.skipped_limit_up || 0} />
                                <Tiny label="确认加仓" value={result.summary.added_position_count || 0} />
                                <Tiny label="仓位方案" value={result.summary.position_mode === 'two_stage_50_50' ? '双仓50/50' : '现行'} />
                            </div>
                            {result.reason && <p className="text-xs font-bold text-slate-400">{result.reason}</p>}
                            {result.meta && (
                                <p className="text-[10px] font-bold text-slate-400">
                                    {result.meta.code} · {result.meta.strategy_type} · {result.meta.data_points} 根K线
                                </p>
                            )}
                        </section>

                        <section className="glass-card p-5 space-y-4 xl:col-span-2">
                            <SectionTitle title="权益曲线" />
                            <div className="h-56 flex items-end gap-2 border-b border-slate-100">
                                {(result.equity_curve.length ? result.equity_curve : [{ date: '--', equity: 100000 }]).map((point, idx) => {
                                    const height = maxEquity === minEquity ? 20 : ((point.equity - minEquity) / (maxEquity - minEquity)) * 170 + 12;
                                    return (
                                        <div key={`${point.date}-${idx}`} className="flex-1 min-w-[10px] flex flex-col items-center justify-end gap-1">
                                            <div
                                                className={cn("w-full rounded-t bg-blue-600", point.equity < 100000 && "bg-rose-500")}
                                                style={{ height }}
                                                title={`${point.date}: ${point.equity}`}
                                            />
                                        </div>
                                    );
                                })}
                            </div>
                        </section>
                    </div>

                    <section className="glass-card p-5 space-y-4">
                        <SectionTitle title="交易明细" />
                        <div className="overflow-hidden rounded-xl border border-slate-100">
                            <table className="w-full text-left text-xs">
                                <thead className="bg-slate-50 text-slate-400 font-black uppercase tracking-wider">
                                    <tr>
                                        <th className="px-3 py-2">入场</th>
                                        <th className="px-3 py-2">信号</th>
                                        <th className="px-3 py-2">出场</th>
                                        <th className="px-3 py-2">价格</th>
                                        <th className="px-3 py-2">收益</th>
                                        <th className="px-3 py-2">天数</th>
                                        <th className="px-3 py-2">原因</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 bg-white">
                                    {result.trades.length === 0 ? (
                                        <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-400 font-bold">暂无成交记录</td></tr>
                                    ) : result.trades.map((trade, idx) => (
                                        <tr key={`${trade.entry_date}-${idx}`}>
                                            <td className="px-3 py-2 font-mono">
                                                {trade.entry_date}
                                                {trade.add_date && <span className="block text-[10px] text-blue-500">加仓 {trade.add_date}</span>}
                                            </td>
                                            <td className="px-3 py-2 font-mono">
                                                {trade.signal_date || trade.entry_date}
                                                {trade.open_gap_pct != null && <span className="ml-1 text-slate-400">({trade.open_gap_pct >= 0 ? '+' : ''}{trade.open_gap_pct}%)</span>}
                                            </td>
                                            <td className="px-3 py-2 font-mono">{trade.exit_date}</td>
                                            <td className="px-3 py-2 font-mono">{trade.entry_price} → {trade.exit_price}</td>
                                            <td className={cn("px-3 py-2 font-black", trade.return_pct >= 0 ? "text-emerald-600" : "text-rose-600")}>{trade.return_pct}%</td>
                                            <td className="px-3 py-2">{trade.hold_days}</td>
                                            <td className="px-3 py-2 text-slate-500">{trade.exit_reason} · {trade.signal_reason}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </section>
                </>
            )}

            {!result && !loading && (
                <div className="glass-card py-16 text-center text-slate-400 font-bold">
                    输入股票和参数后执行回测
                </div>
            )}
        </div>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="space-y-1">
            <span className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</span>
            {children}
        </label>
    );
}

function SectionTitle({ title }: { title: string }) {
    return <h3 className="font-black text-slate-900 flex items-center gap-2"><TrendingUp size={16} /> {title}</h3>;
}

function Metric({ label, value, tone = 'ok' }: { label: string; value: React.ReactNode; tone?: 'ok' | 'warn' | 'error' }) {
    return (
        <div className="glass-card px-4 py-3">
            <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
            <p className={cn("text-xl font-black", tone === 'error' ? "text-rose-600" : tone === 'warn' ? "text-amber-600" : "text-slate-900")}>{value}</p>
        </div>
    );
}

function Tiny({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="rounded-xl bg-slate-50 px-3 py-3">
            <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
            <p className="text-lg font-black text-slate-900">{value}</p>
        </div>
    );
}
