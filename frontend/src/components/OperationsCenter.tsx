"use client";

import React, { useEffect, useState } from 'react';
import { Activity, AlertTriangle, BarChart3, CheckCircle2, Database, FileWarning, Layers3, Loader2, RefreshCw, ServerCog, TrendingDown } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface DataSourceInfo {
    status: string;
    priority: number;
    success_count: number;
    fail_count: number;
    last_error?: string | null;
    last_check?: string | null;
}

interface DataSourceReport {
    status: 'ok' | 'warn' | 'error';
    available_count: number;
    total_count: number;
    sources: Record<string, DataSourceInfo>;
    message: string;
    recommendations: string[];
    local_data?: {
        status: 'ok' | 'warn' | 'error';
        blocking: boolean;
        checks: Array<{ name: string; status: 'ok' | 'warn' | 'error'; message: string }>;
        summary: {
            selected_date?: string;
            stock_count?: number;
            previous_date?: string | null;
            previous_stock_count?: number;
            missing_vs_previous?: number;
            coverage_ratio?: number;
            missing_industry_count?: number;
            abnormal_move_count?: number;
            suspected_corporate_action_gap_count?: number;
            invalid_price_count?: number;
            abnormal_move_samples?: Array<{
                code: string;
                name?: string;
                prev_close?: number | null;
                open?: number | null;
                close?: number | null;
                open_gap_pct?: number | null;
                close_jump_pct?: number | null;
                likely_reason?: string;
            }>;
        };
        recommendations: string[];
    };
}

interface ScanAudit {
    scan_date?: string;
    started_at?: string;
    duration_sec?: number;
    status: string;
    strategy_type?: string;
    total_snapshot?: number;
    candidate_count?: number;
    result_count?: number;
    fail_reasons?: Record<string, number> | string | null;
    error_message?: string | null;
}

interface FailureSample {
    code: string;
    name?: string;
    sample_date?: string;
    strategy_type?: string;
    failure_type?: string;
    reason?: string;
    pnl_pct?: number;
    source?: string;
}

interface OpsSummary {
    status: string;
    scan_quality: {
        total_scans: number;
        success_rate: number;
        avg_duration_sec: number;
        avg_candidates: number;
        avg_results: number;
    };
    failure_reason_top: Array<{ reason: string; count: number }>;
    strategy_distribution: Array<{
        strategy_type: string;
        scan_count: number;
        avg_results: number;
        avg_duration_sec: number;
    }>;
    failure_sample_by_strategy: Array<{
        strategy_type: string;
        count: number;
        avg_pnl_pct: number;
    }>;
    version_distribution: Array<{ version: string; count: number }>;
}

interface ResearchSummary {
    status: string;
    summary: {
        total_signals: number;
        latest_date?: string | null;
        avg_score: number;
        avg_win_rate: number;
    };
    by_strategy: Array<{ strategy_type: string; count: number; avg_score: number; avg_win_rate: number }>;
    by_industry: Array<{ name: string; count: number }>;
    by_regime: Array<{ name: string; count: number }>;
    by_signal: Array<{ name: string; count: number }>;
    by_trade_action: Array<{ name: string; count: number }>;
    score_buckets: Array<{ name: string; count: number }>;
}

interface ApiResponse<T> {
    data: T;
}

export default function OperationsCenter() {
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [dataSources, setDataSources] = useState<DataSourceReport | null>(null);
    const [audits, setAudits] = useState<ScanAudit[]>([]);
    const [failures, setFailures] = useState<FailureSample[]>([]);
    const [summary, setSummary] = useState<OpsSummary | null>(null);
    const [research, setResearch] = useState<ResearchSummary | null>(null);

    const loadData = async () => {
        setLoading(true);
        setLoadError(null);
        try {
            const [sourceRes, auditRes, failureRes, summaryRes, researchRes] = await Promise.allSettled([
                api.get<DataSourceReport>('/api/system/data-sources', { timeout: 8000 }),
                api.get<ScanAudit[]>('/api/system/scan-audits', { params: { limit: 12 }, timeout: 8000 }),
                api.get<FailureSample[]>('/api/system/failure-samples', { params: { limit: 12 }, timeout: 8000 }),
                api.get<OpsSummary>('/api/system/ops-summary', { params: { limit: 60 }, timeout: 8000 }),
                api.get<ResearchSummary>('/api/system/research-summary', { params: { limit: 500 }, timeout: 8000 }),
            ]);

            const failedCount = [sourceRes, auditRes, failureRes, summaryRes, researchRes].filter(item => item.status === 'rejected').length;
            if (isFulfilled<DataSourceReport>(sourceRes)) setDataSources(sourceRes.value.data);
            if (isFulfilled<ScanAudit[]>(auditRes)) setAudits(auditRes.value.data || []);
            if (isFulfilled<FailureSample[]>(failureRes)) setFailures(failureRes.value.data || []);
            if (isFulfilled<OpsSummary>(summaryRes)) setSummary(summaryRes.value.data);
            if (isFulfilled<ResearchSummary>(researchRes)) setResearch(researchRes.value.data);
            if (failedCount > 0) {
                setLoadError(`有 ${failedCount} 个运维接口暂时不可用，已展示可用数据`);
            }
        } catch (err) {
            console.error("Load operations center failed:", err);
            setLoadError("专业驾驶舱加载失败，请确认后端 API 已启动");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadData();
    }, []);

    if (loading) {
        return (
            <div className="glass-card min-h-[360px] flex items-center justify-center text-slate-400">
                <Loader2 className="animate-spin mr-2" /> 正在加载专业驾驶舱...
            </div>
        );
    }

    const latestAudit = audits[0];
    const maxFailureCount = Math.max(...(summary?.failure_reason_top || []).map(item => item.count), 1);
    const maxStrategyScans = Math.max(...(summary?.strategy_distribution || []).map(item => item.scan_count), 1);
    const maxResearchSignals = Math.max(...(research?.by_strategy || []).map(item => item.count), 1);

    return (
        <div className="space-y-5 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="workspace-panel px-5 py-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="w-11 h-11 rounded-xl bg-slate-900 text-white flex items-center justify-center">
                        <ServerCog size={22} />
                    </div>
                    <div>
                        <h2 className="text-lg font-black text-slate-900">专业驾驶舱</h2>
                        <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Ops, Audit & Research Quality</p>
                    </div>
                </div>
                <button onClick={loadData} className="toolbar-button">
                    <RefreshCw size={16} />
                    刷新
                </button>
            </div>

            {loadError && (
                <div className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm font-bold text-amber-800">
                        <AlertTriangle size={16} />
                        <span>{loadError}</span>
                    </div>
                    <button onClick={loadData} className="text-xs font-black text-amber-800 hover:text-amber-950">
                        重试
                    </button>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-3">
                <MetricCard icon={<Database size={18} />} label="可用数据源" value={`${dataSources?.available_count || 0}/${dataSources?.total_count || 0}`} tone={dataSources?.status} />
                <MetricCard icon={<Activity size={18} />} label="扫描成功率" value={`${summary?.scan_quality.success_rate ?? 0}%`} tone={(summary?.scan_quality.success_rate ?? 0) >= 80 ? 'ok' : 'warn'} />
                <MetricCard icon={<Layers3 size={18} />} label="研究样本" value={research?.summary.total_signals ?? 0} />
                <MetricCard icon={<AlertTriangle size={18} />} label="最近入选 / 候选" value={`${latestAudit?.result_count ?? '--'} / ${latestAudit?.candidate_count ?? '--'}`} />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
                <section className="glass-card p-5 space-y-4">
                    <SectionTitle icon={<BarChart3 size={18} />} title="扫描质量" subtitle="最近审计任务的稳定性和产出密度" />
                    <div className="grid grid-cols-3 gap-2">
                        <TinyStat label="任务数" value={summary?.scan_quality.total_scans ?? 0} />
                        <TinyStat label="均候选" value={summary?.scan_quality.avg_candidates ?? 0} />
                        <TinyStat label="均入选" value={summary?.scan_quality.avg_results ?? 0} />
                    </div>
                    <div className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3">
                        <div className="flex items-center justify-between text-xs font-black text-slate-500">
                            <span>平均耗时</span>
                            <span>{summary?.scan_quality.avg_duration_sec ?? 0}s</span>
                        </div>
                        <div className="mt-2 h-2 rounded-full bg-white overflow-hidden">
                            <div
                                className="h-full rounded-full bg-blue-600"
                                style={{ width: `${Math.min((summary?.scan_quality.success_rate ?? 0), 100)}%` }}
                            />
                        </div>
                    </div>
                </section>

                <section className="glass-card p-5 space-y-4">
                    <SectionTitle icon={<TrendingDown size={18} />} title="失败原因 Top" subtitle="扫描过滤最集中的原因" />
                    <div className="space-y-3">
                        {(summary?.failure_reason_top || []).length === 0 ? (
                            <EmptyLine label="暂无失败原因分布" />
                        ) : summary?.failure_reason_top.map(item => (
                            <div key={item.reason} className="space-y-1">
                                <div className="flex items-center justify-between text-xs font-bold">
                                    <span className="text-slate-700 truncate pr-3">{item.reason}</span>
                                    <span className="font-mono text-slate-500">{item.count}</span>
                                </div>
                                <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                                    <div className="h-full rounded-full bg-rose-500" style={{ width: `${Math.max(8, item.count / maxFailureCount * 100)}%` }} />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>

                <section className="glass-card p-5 space-y-4">
                    <SectionTitle icon={<Activity size={18} />} title="策略产出分布" subtitle="按策略类型聚合扫描频率和平均入选数" />
                    <div className="space-y-3">
                        {(summary?.strategy_distribution || []).length === 0 ? (
                            <EmptyLine label="暂无策略审计数据" />
                        ) : summary?.strategy_distribution.map(item => (
                            <div key={item.strategy_type} className="rounded-xl border border-slate-100 bg-white px-3 py-2">
                                <div className="flex items-center justify-between gap-3">
                                    <span className="text-sm font-black text-slate-800">{item.strategy_type}</span>
                                    <span className="text-[10px] font-black text-slate-400">均入选 {item.avg_results} · {item.avg_duration_sec}s</span>
                                </div>
                                <div className="mt-2 h-2 rounded-full bg-slate-100 overflow-hidden">
                                    <div className="h-full rounded-full bg-violet-600" style={{ width: `${Math.max(8, item.scan_count / maxStrategyScans * 100)}%` }} />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            </div>

            <section className="glass-card p-5 space-y-4">
                <SectionTitle icon={<Layers3 size={18} />} title="扫描结果画像" subtitle={`最新日期 ${research?.summary.latest_date || '--'} · 平均分 ${research?.summary.avg_score ?? 0} · 平均胜率 ${research?.summary.avg_win_rate ?? 0}%`} />
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                    <div className="space-y-3">
                        <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">策略样本分布</p>
                        {(research?.by_strategy || []).length === 0 ? (
                            <EmptyLine label="暂无扫描结果画像" />
                        ) : research?.by_strategy.map(item => (
                            <div key={item.strategy_type} className="rounded-xl border border-slate-100 bg-white px-3 py-2">
                                <div className="flex items-center justify-between gap-3">
                                    <span className="text-sm font-black text-slate-800">{item.strategy_type}</span>
                                    <span className="text-[10px] font-black text-slate-400">均分 {item.avg_score} · 胜率 {item.avg_win_rate}%</span>
                                </div>
                                <div className="mt-2 h-2 rounded-full bg-slate-100 overflow-hidden">
                                    <div className="h-full rounded-full bg-blue-600" style={{ width: `${Math.max(8, item.count / maxResearchSignals * 100)}%` }} />
                                </div>
                            </div>
                        ))}
                    </div>
                    <DistributionList title="Brooks结构" items={research?.by_regime || []} />
                    <DistributionList title="交易动作" items={research?.by_trade_action || []} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <TagCloud title="信号类型" items={research?.by_signal || []} />
                    <TagCloud title="行业集中" items={research?.by_industry || []} />
                    <TagCloud title="分数桶" items={research?.score_buckets || []} />
                </div>
            </section>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
                <section className="glass-card p-5 space-y-4">
                    <SectionTitle icon={<Database size={18} />} title="数据源质量" subtitle={dataSources?.message || '暂无数据'} />
                    <div className="space-y-2">
                        {Object.entries(dataSources?.sources || {}).map(([name, info]) => (
                            <div key={name} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="min-w-0">
                                        <p className="text-sm font-black text-slate-800 truncate">{name}</p>
                                        <p className="text-[10px] font-bold text-slate-400">优先级 {info.priority} · 成功 {info.success_count} · 失败 {info.fail_count}</p>
                                    </div>
                                    <StatusPill status={info.status === 'available' ? 'ok' : 'warn'} label={info.status} />
                                </div>
                                {info.last_error && <p className="mt-2 text-[10px] text-rose-500 font-medium truncate">{info.last_error}</p>}
                            </div>
                        ))}
                    </div>
                    {(dataSources?.recommendations || []).length > 0 && (
                        <div className="rounded-xl bg-amber-50 border border-amber-100 px-3 py-2 text-[11px] font-bold text-amber-700">
                            {dataSources?.recommendations.slice(0, 2).join('；')}
                        </div>
                    )}
                </section>

                <section className="glass-card p-5 space-y-4 xl:col-span-2">
                    <SectionTitle
                        icon={<FileWarning size={18} />}
                        title="本地行情体检"
                        subtitle={`最新 ${dataSources?.local_data?.summary?.selected_date || '--'} · 覆盖率 ${dataSources?.local_data?.summary?.coverage_ratio ?? '--'}%`}
                    />
                    <div className="grid grid-cols-2 lg:grid-cols-6 gap-2">
                        <TinyStat label="最新覆盖" value={dataSources?.local_data?.summary?.stock_count ?? 0} />
                        <TinyStat label="较前缺失" value={dataSources?.local_data?.summary?.missing_vs_previous ?? 0} />
                        <TinyStat label="行业缺失" value={dataSources?.local_data?.summary?.missing_industry_count ?? 0} />
                        <TinyStat label="异常跳变" value={dataSources?.local_data?.summary?.abnormal_move_count ?? 0} />
                        <TinyStat label="除权断点" value={dataSources?.local_data?.summary?.suspected_corporate_action_gap_count ?? 0} />
                        <TinyStat label="无效K线" value={dataSources?.local_data?.summary?.invalid_price_count ?? 0} />
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                        {(dataSources?.local_data?.checks || []).map(check => (
                            <div key={check.name} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 flex items-start justify-between gap-3">
                                <p className="text-xs font-bold text-slate-600 leading-relaxed">{check.message}</p>
                                <StatusPill status={check.status} label={check.status} />
                            </div>
                        ))}
                    </div>
                    {(dataSources?.local_data?.recommendations || []).length > 0 && (
                        <div className="rounded-xl bg-amber-50 border border-amber-100 px-3 py-2 text-[11px] font-bold text-amber-700">
                            {dataSources?.local_data?.recommendations?.slice(0, 3).join('；')}
                        </div>
                    )}
                    {(dataSources?.local_data?.summary?.abnormal_move_samples || []).length > 0 && (
                        <div className="overflow-hidden rounded-xl border border-slate-100">
                            <table className="w-full text-left text-[11px]">
                                <thead className="bg-slate-50 text-slate-400 font-black">
                                    <tr>
                                        <th className="px-3 py-2">股票</th>
                                        <th className="px-3 py-2">前收</th>
                                        <th className="px-3 py-2">今开</th>
                                        <th className="px-3 py-2">今收</th>
                                        <th className="px-3 py-2">跳变</th>
                                        <th className="px-3 py-2">归因</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 bg-white">
                                    {dataSources?.local_data?.summary?.abnormal_move_samples?.slice(0, 5).map(item => (
                                        <tr key={item.code}>
                                            <td className="px-3 py-2 font-black text-slate-700">{item.name || item.code}</td>
                                            <td className="px-3 py-2 font-mono">{item.prev_close ?? '--'}</td>
                                            <td className="px-3 py-2 font-mono">{item.open ?? '--'}</td>
                                            <td className="px-3 py-2 font-mono">{item.close ?? '--'}</td>
                                            <td className="px-3 py-2 font-mono text-rose-600">{item.close_jump_pct != null ? `${item.close_jump_pct}%` : '--'}</td>
                                            <td className="px-3 py-2">
                                                <span className="rounded-md bg-slate-50 px-2 py-1 font-black text-slate-500">
                                                    {item.likely_reason === 'suspected_corporate_action_gap' ? '除权复权断点' : '待核查'}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </section>

                <section className="glass-card p-5 space-y-4 xl:col-span-3">
                    <SectionTitle icon={<Activity size={18} />} title="扫描审计日志" subtitle="最近任务、耗时、候选与失败原因" />
                    <div className="overflow-hidden rounded-xl border border-slate-100">
                        <table className="w-full text-left text-xs">
                            <thead className="bg-slate-50 text-slate-400 font-black uppercase tracking-wider">
                                <tr>
                                    <th className="px-3 py-2">日期</th>
                                    <th className="px-3 py-2">策略</th>
                                    <th className="px-3 py-2">候选</th>
                                    <th className="px-3 py-2">入选</th>
                                    <th className="px-3 py-2">耗时</th>
                                    <th className="px-3 py-2">状态</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {audits.length === 0 ? (
                                    <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-400 font-bold">暂无审计日志</td></tr>
                                ) : audits.map((audit, idx) => (
                                    <tr key={`${audit.started_at}-${idx}`} className="bg-white">
                                        <td className="px-3 py-2 font-bold text-slate-700">{audit.scan_date || '--'}</td>
                                        <td className="px-3 py-2 text-slate-500">{audit.strategy_type || '--'}</td>
                                        <td className="px-3 py-2 font-mono">{audit.candidate_count ?? 0}</td>
                                        <td className="px-3 py-2 font-mono text-blue-700 font-black">{audit.result_count ?? 0}</td>
                                        <td className="px-3 py-2 font-mono">{audit.duration_sec ?? '--'}s</td>
                                        <td className="px-3 py-2"><StatusPill status={audit.status === 'SUCCESS' ? 'ok' : 'error'} label={audit.status} /></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>
            </div>

            <section className="glass-card p-5 space-y-4">
                <SectionTitle icon={<FileWarning size={18} />} title="失败样本库" subtitle="亏损平仓和后续失败信号会沉淀到这里" />
                {(summary?.failure_sample_by_strategy || []).length > 0 && (
                    <div className="flex flex-wrap gap-2">
                        {summary?.failure_sample_by_strategy.map(item => (
                            <span key={item.strategy_type} className="inline-flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-2 text-[11px] font-black text-rose-700">
                                {item.strategy_type} {item.count}笔 · 均{item.avg_pnl_pct}%
                            </span>
                        ))}
                    </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                    {failures.length === 0 ? (
                        <div className="col-span-full rounded-xl border border-dashed border-slate-200 py-10 text-center text-slate-400 font-bold">
                            暂无失败样本
                        </div>
                    ) : failures.map((item, idx) => (
                        <div key={`${item.code}-${item.sample_date}-${idx}`} className="rounded-xl border border-slate-100 bg-white px-4 py-3">
                            <div className="flex items-center justify-between">
                                <div>
                                    <p className="text-sm font-black text-slate-800">{item.name || item.code}</p>
                                    <p className="text-[10px] font-bold text-slate-400">{item.code} · {item.strategy_type || '--'} · {item.sample_date || '--'}</p>
                                </div>
                                <span className="text-sm font-black text-rose-600">{item.pnl_pct != null ? `${item.pnl_pct}%` : '--'}</span>
                            </div>
                            <p className="mt-2 text-[11px] text-slate-500 line-clamp-2">{item.reason || item.failure_type || '未记录原因'}</p>
                        </div>
                    ))}
                </div>
            </section>

            {(summary?.version_distribution || []).length > 0 && (
                <section className="glass-card p-5 space-y-4">
                    <SectionTitle icon={<CheckCircle2 size={18} />} title="版本口径分布" subtitle="最近扫描使用过的策略、回测和退出规则版本" />
                    <div className="flex flex-wrap gap-2">
                        {summary?.version_distribution.map(item => (
                            <span key={item.version} className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-[11px] font-black text-slate-600">
                                {item.version} · {item.count}
                            </span>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}

function isFulfilled<T>(result: PromiseSettledResult<ApiResponse<T>>): result is PromiseFulfilledResult<ApiResponse<T>> {
    return result.status === 'fulfilled';
}

function MetricCard({ icon, label, value, tone = 'ok' }: { icon: React.ReactNode; label: string; value: React.ReactNode; tone?: string }) {
    return (
        <div className="glass-card px-4 py-3 flex items-center gap-3">
            <div className={cn("w-9 h-9 rounded-lg flex items-center justify-center", tone === 'error' ? "bg-rose-50 text-rose-600" : tone === 'warn' ? "bg-amber-50 text-amber-600" : "bg-blue-50 text-blue-700")}>
                {icon}
            </div>
            <div>
                <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
                <p className="text-lg font-black text-slate-900">{value}</p>
            </div>
        </div>
    );
}

function SectionTitle({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle: string }) {
    return (
        <div className="flex items-center gap-3">
            <div className="text-slate-500">{icon}</div>
            <div>
                <h3 className="font-black text-slate-900">{title}</h3>
                <p className="text-[10px] font-bold text-slate-400">{subtitle}</p>
            </div>
        </div>
    );
}

function TinyStat({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="rounded-xl bg-slate-50 px-3 py-3">
            <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">{label}</p>
            <p className="text-lg font-black text-slate-900">{value}</p>
        </div>
    );
}

function DistributionList({ title, items }: { title: string; items: Array<{ name: string; count: number }> }) {
    const maxCount = Math.max(...items.map(item => item.count), 1);
    return (
        <div className="space-y-3">
            <p className="text-[10px] font-black uppercase tracking-wider text-slate-400">{title}</p>
            {items.length === 0 ? (
                <EmptyLine label="暂无分布数据" />
            ) : items.slice(0, 5).map(item => (
                <div key={item.name} className="space-y-1">
                    <div className="flex items-center justify-between text-xs font-bold">
                        <span className="text-slate-700 truncate pr-3">{item.name}</span>
                        <span className="font-mono text-slate-500">{item.count}</span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                        <div className="h-full rounded-full bg-emerald-600" style={{ width: `${Math.max(8, item.count / maxCount * 100)}%` }} />
                    </div>
                </div>
            ))}
        </div>
    );
}

function TagCloud({ title, items }: { title: string; items: Array<{ name: string; count: number }> }) {
    return (
        <div className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3">
            <p className="text-[10px] font-black uppercase tracking-wider text-slate-400 mb-2">{title}</p>
            <div className="flex flex-wrap gap-2">
                {items.length === 0 ? (
                    <span className="text-xs font-bold text-slate-400">暂无数据</span>
                ) : items.slice(0, 6).map(item => (
                    <span key={item.name} className="rounded-md bg-white px-2 py-1 text-[11px] font-black text-slate-600 border border-slate-100">
                        {item.name} · {item.count}
                    </span>
                ))}
            </div>
        </div>
    );
}

function EmptyLine({ label }: { label: string }) {
    return (
        <div className="rounded-xl border border-dashed border-slate-200 py-7 text-center text-xs font-bold text-slate-400">
            {label}
        </div>
    );
}

function StatusPill({ status, label }: { status: 'ok' | 'warn' | 'error'; label: string }) {
    return (
        <span className={cn(
            "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-black",
            status === 'ok' && "bg-emerald-50 text-emerald-700",
            status === 'warn' && "bg-amber-50 text-amber-700",
            status === 'error' && "bg-rose-50 text-rose-700",
        )}>
            {status === 'ok' ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
            {label}
        </span>
    );
}
