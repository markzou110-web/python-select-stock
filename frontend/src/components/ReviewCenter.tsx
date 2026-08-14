"use client";

import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, CalendarDays, Copy, Download, Globe2, Loader2, RefreshCw, Target, TrendingUp } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

type LayerMetric = {
    signals?: number;
    win_rate?: number;
    avg_return?: number | null;
};

type ProfitabilityLayer = {
    key: string;
    label: string;
    source: string;
    rows: number;
    metrics?: Record<string, LayerMetric>;
    verdict?: {
        status?: string;
        action?: string;
    };
};

type ProfitabilityPayload = {
    summary?: {
        best_layer?: string;
        positive_layers?: number;
        weak_layers?: number;
    };
    layers?: ProfitabilityLayer[];
    real_trade_execution?: ExecutionReview;
    measurement_contract?: MeasurementContract;
};

type MeasurementContract = {
    sample_unit?: string;
    entry_price?: string;
    risk_model?: string;
    maturity_rule?: string;
    return_type?: string;
    benchmark_adjusted?: boolean;
};

type ExecutionReview = {
    sample?: number;
    system_bark_sample?: number;
    execution_gap_5d?: number | null;
    avg_entry_slippage_pct?: number | null;
    unknown_plan_adherence?: number;
    missing_planned_entry?: number;
    missing_signal_date?: number;
    execution_quality_score?: number;
    diagnostics?: string[];
};

type CalibrationRow = {
    value?: string;
    signals?: number;
    mature_5d?: number;
    metrics?: Record<string, LayerMetric>;
    excursion_5d?: {
        samples?: number;
        avg_mfe?: number | null;
        avg_mae?: number | null;
        mfe_ge_5_rate?: number | null;
        mae_le_minus_4_rate?: number | null;
    };
};

type CalibrationPayload = {
    summary?: { signals?: number; mature_5d?: number; mature_10d?: number };
    grade_monotonicity?: {
        status?: string;
        reason?: string;
        samples?: Record<string, number>;
        values?: Record<string, number>;
    };
    by_confirmation_event?: CalibrationRow[];
    by_early_value_transition?: CalibrationRow[];
    bottom_discovery_analysis?: {
        status?: string;
        min_mature_b1_samples?: number;
        by_stage?: CalibrationRow[];
        conversion?: {
            b0_unique_stocks?: number;
            converted_to_b1?: number;
            conversion_rate?: number | null;
            median_wait_calendar_days?: number | null;
        };
        formal_confirmation?: {
            window_calendar_days?: number;
            b1_unique_stocks?: number;
            mature_b1_followups?: number;
            confirmed_stocks?: number;
            confirmation_rate?: number | null;
            median_wait_calendar_days?: number | null;
            within_10_days?: number;
        };
        strict_strategy_lead?: {
            window_calendar_days?: number;
            mature_b1_followups?: number;
            matched_stocks?: number;
            match_rate?: number | null;
            median_lead_calendar_days?: number | null;
        };
    };
    blocker_analysis?: {
        summary?: { review_rules?: number; valid_filters?: number };
        items?: Array<{
            blocker?: string;
            occurrences?: number;
            recommendation?: string;
            hit_minus_miss_avg_return?: number | null;
            controlled_comparison?: {
                paired_samples?: number;
                hit_minus_miss_avg_return?: number | null;
            };
        }>;
    };
    measurement_contract?: MeasurementContract;
};

type SectorWatchMetric = {
    signals?: number;
    win_rate?: number;
    avg_return?: number | null;
    best_return?: number | null;
    worst_return?: number | null;
};

type SectorWatchStateRow = {
    state?: string;
    label?: string;
    items?: number;
    mature_5d?: number;
    metrics?: Record<string, SectorWatchMetric>;
};

type SectorWatchPerformance = {
    source?: string;
    summary?: {
        items?: number;
        mature_5d?: number;
        best_state?: string;
    };
    by_state?: SectorWatchStateRow[];
    suggestions?: string[];
    notes?: string[];
};

type RecommendationOutcomeLoop = {
    summary?: {
        events?: number;
        mature_5d?: number;
        best_source?: string;
        worst_source?: string;
        days?: number;
        boost_count?: number;
        downweight_count?: number;
    };
    horizons?: Record<string, LayerMetric>;
    by_source?: Array<{
        source?: string;
        events?: number;
        mature_5d?: number;
        metrics?: Record<string, LayerMetric>;
    }>;
    by_strategy?: Array<{
        strategy_type?: string;
        events?: number;
        mature_5d?: number;
        metrics?: Record<string, LayerMetric>;
    }>;
    adjustments?: {
        by_source?: OutcomeAdjustment[];
        by_strategy?: OutcomeAdjustment[];
        rules?: string[];
    };
    recent_events?: Array<{
        signal_date?: string;
        source?: string;
        code?: string;
        name?: string;
        strategy_type?: string;
        trade_bucket?: string;
        pa_trade_setup?: string;
        ret_1d?: number | null;
        ret_3d?: number | null;
        ret_5d?: number | null;
        ret_10d?: number | null;
    }>;
    suggestions?: string[];
    measurement_contract?: MeasurementContract;
};

type OutcomeAdjustment = {
    dimension?: string;
    value?: string;
    action?: 'BOOST' | 'DOWNWEIGHT' | 'OBSERVE' | 'KEEP' | string;
    score_delta?: number;
    mature_5d?: number;
    win_rate_5d?: number;
    avg_return_5d?: number | null;
    reason?: string;
};

type DailyStrategyReport = {
    scan_date?: string;
    summary?: {
        scan_count?: number;
        trade_count?: number;
        early_count?: number;
        observe_count?: number;
        block_count?: number;
        bark_push_count?: number;
        watchlist_count?: number;
        real_position_count?: number;
        stance?: string;
    };
    top_candidates?: Array<{
        code?: string;
        name?: string;
        industry?: string;
        grade?: string;
        bucket?: string;
        score?: number;
        blockers?: string[];
    }>;
    sector_push_gaps?: Array<{
        industry?: string;
        primary_reason_label?: string;
        sector_momentum_score?: number;
        scan_candidate_count?: number;
    }>;
    next_actions?: string[];
};

type ResearchContext = {
    as_of?: string;
    contract_version?: string;
    policy?: string;
    market?: {
        global_indices?: {
            status?: string;
            source?: string;
            items?: Array<{ name?: string; price?: number | null; pct?: number | null; market_time?: string }>;
        };
    };
    decision?: { scan_date?: string; summary?: { trade_count?: number; early_count?: number; observe_count?: number } };
    strategy_health?: Record<string, { status?: string; signals?: number; expected_return?: number }>;
    candidate_evidence?: { summary?: { targets?: number; evidence?: number; risk?: number; catalyst?: number } };
    analysis_framework?: string[];
};

type SignalMetric = {
    signals?: number;
    avg_return?: number;
    win_rate?: number;
};

type SignalPerformanceCohort = {
    cohort?: string;
    alert_to_close?: SignalMetric;
    ret_5d?: SignalMetric;
};

type SignalPerformanceItem = {
    signal_id?: string;
    code?: string;
    name?: string;
    grade?: string;
    sop_base_grade?: string;
    sop_quality_score?: number;
    display_quality_score?: number;
    sop_quality_gap_to_a?: number;
    sop_grade_reason?: string;
};

type SignalPerformancePayload = {
    status?: string;
    note?: string;
    summary?: Record<string, number>;
    validation?: Record<string, {
        status?: string;
        mature_5d?: number;
        triggered_samples?: number;
        required?: number;
    }>;
    strong_stock_coverage?: Record<string, number>;
    gate_attribution?: Record<string, number>;
    cohorts?: SignalPerformanceCohort[];
    items?: SignalPerformanceItem[];
};

export default function ReviewCenter() {
    const [data, setData] = useState<any>(null);
    const [profitability, setProfitability] = useState<ProfitabilityPayload | null>(null);
    const [sectorWatchPerformance, setSectorWatchPerformance] = useState<SectorWatchPerformance | null>(null);
    const [recommendationLoop, setRecommendationLoop] = useState<RecommendationOutcomeLoop | null>(null);
    const [dailyReport, setDailyReport] = useState<DailyStrategyReport | null>(null);
    const [calibration, setCalibration] = useState<CalibrationPayload | null>(null);
    const [executionReplay, setExecutionReplay] = useState<any>(null);
    const [signalPerformance, setSignalPerformance] = useState<SignalPerformancePayload | null>(null);
    const [researchContext, setResearchContext] = useState<ResearchContext | null>(null);
    const [followup, setFollowup] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [followupLoading, setFollowupLoading] = useState(false);
    const [days, setDays] = useState(120);
    const [historyDates, setHistoryDates] = useState<string[]>([]);
    const [followupDate, setFollowupDate] = useState('');

    const fetchData = async () => {
        setLoading(true);
        try {
            const results = await Promise.allSettled([
                api.get(`/api/review/scan-performance?days=${days}`),
                api.get(`/api/review/profitability-dashboard?days=${days}`),
                api.get(`/api/review/sector-watch-performance?days=${days}`),
                api.get(`/api/review/recommendation-outcome-loop?days=${days}`),
                api.get('/api/review/daily-strategy-report'),
                api.get(`/api/review/strategy-calibration-report?days=${days}`),
                api.get('/api/review/research-context'),
                api.get(`/api/review/execution-policy-replay?days=${days}`),
                api.get(`/api/system/signal-performance?days=${days}`),
            ]);
            const value = (index: number) => results[index].status === 'fulfilled' ? (results[index] as PromiseFulfilledResult<any>).value.data : null;
            if (value(0)) setData(value(0));
            if (value(1)) setProfitability(value(1));
            if (value(2)) setSectorWatchPerformance(value(2));
            if (value(3)) setRecommendationLoop(value(3));
            if (value(4)) setDailyReport(value(4));
            if (value(5)) setCalibration(value(5));
            if (value(6)) setResearchContext(value(6));
            if (value(7)) setExecutionReplay(value(7));
            if (value(8)) setSignalPerformance(value(8));
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

            <DailyStrategyReportCard data={dailyReport} />

            <ResearchContextCard data={researchContext} />

            <ProfitabilityLayerCard data={profitability} />

            <ExecutionReviewCard data={profitability?.real_trade_execution} contract={profitability?.measurement_contract} />

            <CalibrationCard data={calibration} />

            <ExecutionReplayCard data={executionReplay} />

            <SignalPerformanceCard data={signalPerformance} />

            <RecommendationOutcomeLoopCard data={recommendationLoop} />

            <SectorWatchPerformanceCard data={sectorWatchPerformance} />

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

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="情绪阶段表现" rows={data?.by_market_sentiment || []} nameKey="stage" />
                <TableCard title="机会分区间表现" rows={data?.by_opportunity_bucket || []} nameKey="bucket" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                <TableCard title="板块阶段表现" rows={data?.by_sector_phase || []} nameKey="phase" />
                <TableCard title="板块角色表现" rows={data?.by_sector_role || []} nameKey="role" />
                <TableCard title="板块联动表现" rows={data?.by_sector_alignment || []} nameKey="bucket" />
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <TableCard title="主流级别表现" rows={data?.by_sector_mainline || []} nameKey="mainline" />
                <TableCard title="交易状态表现" rows={data?.by_trade_state || []} nameKey="state" />
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

function ResearchContextCard({ data }: { data: ResearchContext | null }) {
    const [copied, setCopied] = useState(false);
    const globalItems = data?.market?.global_indices?.items || [];
    const healthRows = Object.entries(data?.strategy_health || {});
    const evidence = data?.candidate_evidence?.summary || {};

    const copyContext = async () => {
        if (!data) return;
        await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1800);
    };

    return (
        <div className="glass-card p-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <div className="flex items-center gap-2"><Globe2 size={18} className="text-indigo-600" /><h3 className="font-black text-slate-800">AI 研究上下文</h3></div>
                    <p className="mt-1 text-xs font-bold text-slate-400">模型中立的市场、策略健康、候选与资讯证据快照</p>
                </div>
                <button type="button" onClick={copyContext} disabled={!data} className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-black text-slate-600 hover:text-indigo-600 disabled:opacity-50">
                    <Copy size={14} />{copied ? '已复制' : '复制上下文'}
                </button>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
                <div>
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">全球市场</div>
                    <div className="grid grid-cols-2 gap-2">
                        {globalItems.map(item => (
                            <div key={item.name} className="rounded-md border border-slate-100 bg-white px-3 py-2">
                                <p className="text-[10px] font-black text-slate-400">{item.name}</p>
                                <p className={cn("mt-1 text-sm font-black", Number(item.pct || 0) >= 0 ? 'text-rose-600' : 'text-emerald-600')}>
                                    {item.price ?? '--'} · {item.pct == null ? '--' : `${item.pct >= 0 ? '+' : ''}${item.pct}%`}
                                </p>
                            </div>
                        ))}
                        {!globalItems.length && <p className="col-span-2 text-xs font-bold text-slate-400">全球指数暂不可用，不影响A股策略运行</p>}
                    </div>
                </div>
                <div>
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">策略健康</div>
                    <div className="space-y-2">
                        {healthRows.slice(0, 6).map(([strategy, row]) => (
                            <div key={strategy} className="flex items-center justify-between border-b border-slate-100 pb-2 text-xs last:border-b-0">
                                <span className="font-black text-slate-700">{strategy}</span>
                                <span className={cn("font-black", row.status === 'ACTIVE' ? 'text-rose-600' : row.status === 'PAUSED' ? 'text-emerald-700' : 'text-amber-700')}>{row.status || 'UNKNOWN'} · {row.signals || 0}</span>
                            </div>
                        ))}
                    </div>
                </div>
                <div>
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">候选证据</div>
                    <div className="grid grid-cols-2 gap-2">
                        <MiniStat label="关联标的" value={`${evidence.targets || 0}`} />
                        <MiniStat label="证据" value={`${evidence.evidence || 0}`} />
                        <MiniStat label="风险" value={`${evidence.risk || 0}`} />
                        <MiniStat label="催化" value={`${evidence.catalyst || 0}`} hot={(evidence.catalyst || 0) > 0} />
                    </div>
                </div>
            </div>
            <div className="mt-3 rounded-md border border-slate-100 bg-slate-50/70 px-3 py-2 text-[10px] font-bold text-slate-400">
                {data?.policy || '只读研究上下文，不修改策略参数、评分、A级或交易资格'}{data?.as_of ? ` · ${String(data.as_of).slice(0, 19)}` : ''}
            </div>
        </div>
    );
}

function RecommendationOutcomeLoopCard({ data }: { data: RecommendationOutcomeLoop | null }) {
    const summary = data?.summary || {};
    const horizons = data?.horizons || {};
    const bySource = data?.by_source || [];
    const recent = data?.recent_events || [];
    const suggestions = data?.suggestions || [];
    const adjustmentRows = [
        ...(data?.adjustments?.by_source || []),
        ...(data?.adjustments?.by_strategy || []),
    ].filter(row => row.action && row.action !== 'KEEP').slice(0, 8);

    return (
        <div className="glass-card p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between mb-5">
                <div>
                    <h3 className="font-black text-slate-800">推荐收益闭环</h3>
                    <p className="text-xs font-bold text-slate-400 mt-1">验证 Bark/扫描推荐后 1/3/5/10 日真实表现</p>
                </div>
                <div className="grid grid-cols-4 gap-2 min-w-full lg:min-w-[560px]">
                    <MiniStat label="推荐事件" value={`${summary.events || 0}`} />
                    <MiniStat label="5日成熟" value={`${summary.mature_5d || 0}`} />
                    <MiniStat label="最佳来源" value={summary.best_source || '暂无'} hot />
                    <MiniStat label="薄弱来源" value={summary.worst_source || '暂无'} />
                </div>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
                {['1d', '3d', '5d', '10d'].map(key => (
                    <div key={key} className="rounded-md border border-slate-100 bg-slate-50/70 px-3 py-2">
                        <div className="text-[10px] font-black text-slate-400">{key.toUpperCase()} 表现</div>
                        <div className="mt-1">{formatLayerMetric(horizons[key])}</div>
                    </div>
                ))}
            </div>

            <div className="mb-5 rounded-md border border-slate-100 bg-white/70 px-3 py-2">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">调参提示</div>
                <div className="mt-1 text-xs font-bold text-slate-600">
                    {suggestions.length ? suggestions.slice(0, 2).join('；') : '等待更多推荐事件成熟后用于调权'}
                </div>
            </div>

            <div className="mb-5 rounded-md border border-slate-100 bg-slate-50/70 px-3 py-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">自动调权建议</div>
                    <div className="text-[10px] font-bold text-slate-400">加权 {summary.boost_count || 0} · 降权 {summary.downweight_count || 0}</div>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
                    {adjustmentRows.map((row, idx) => (
                        <div key={`${row.dimension}-${row.value}-${idx}`} className="rounded-md border border-white bg-white px-3 py-2">
                            <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                    <div className="text-xs font-black text-slate-800 truncate">{row.dimension} · {row.value}</div>
                                    <div className="mt-0.5 text-[10px] font-bold text-slate-400">
                                        样本 {row.mature_5d || 0} · 胜率 {row.win_rate_5d ?? 0}% · 均收 {formatSignedPct(row.avg_return_5d)}
                                    </div>
                                </div>
                                <span className={cn("shrink-0 rounded-md border px-2 py-1 text-[10px] font-black", adjustmentTone(row.action))}>
                                    {adjustmentLabel(row.action)} {formatScoreDelta(row.score_delta)}
                                </span>
                            </div>
                            <div className="mt-1 text-[10px] font-bold text-slate-500">{row.reason || '--'}</div>
                        </div>
                    ))}
                </div>
                {adjustmentRows.length === 0 && <div className="py-6 text-center text-slate-400 text-xs font-bold">暂无需要调权的来源或策略</div>}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                <div className="overflow-x-auto">
                    <table className="w-full text-left">
                        <thead>
                            <tr className="text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
                                <th className="py-2 pr-3">来源</th>
                                <th className="py-2 pr-3">样本</th>
                                <th className="py-2 pr-3">1日</th>
                                <th className="py-2 pr-3">5日</th>
                                <th className="py-2 pr-3">10日</th>
                            </tr>
                        </thead>
                        <tbody>
                            {bySource.slice(0, 6).map(row => (
                                <tr key={row.source || 'UNKNOWN'} className="border-b border-slate-50 last:border-b-0 text-xs">
                                    <td className="py-3 pr-3">
                                        <div className="font-black text-slate-800">{row.source || 'UNKNOWN'}</div>
                                        <div className="text-[10px] font-bold text-slate-400">{row.mature_5d || 0} 个5日成熟</div>
                                    </td>
                                    <td className="py-3 pr-3 font-black text-slate-700">{row.events || 0}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(row.metrics?.['1d'])}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(row.metrics?.['5d'])}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(row.metrics?.['10d'])}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {bySource.length === 0 && <div className="py-12 text-center text-slate-400 font-bold">暂无来源闭环样本</div>}
                </div>

                <div className="space-y-2">
                    {recent.slice(0, 6).map((row, idx) => (
                        <div key={`${row.code}-${row.signal_date}-${idx}`} className="rounded-md border border-slate-100 bg-slate-50/60 px-3 py-2">
                            <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                    <div className="text-sm font-black text-slate-800 truncate">{row.name || row.code || '--'}</div>
                                    <div className="text-[10px] font-bold text-slate-400">
                                        {row.signal_date || '--'} · {row.source || '--'} · {row.strategy_type || '--'}
                                    </div>
                                </div>
                                <div className={cn("text-sm font-black", Number(row.ret_5d || 0) >= 0 ? "text-rose-600" : "text-emerald-600")}>
                                    {formatSignedPct(row.ret_5d)}
                                </div>
                            </div>
                            <div className="mt-1 text-[10px] font-bold text-slate-500">
                                {row.trade_bucket || 'UNKNOWN'} · {row.pa_trade_setup || '未记录形态'} · 1日 {formatSignedPct(row.ret_1d)} / 10日 {formatSignedPct(row.ret_10d)}
                            </div>
                        </div>
                    ))}
                    {recent.length === 0 && <div className="py-12 text-center text-slate-400 font-bold">暂无最近推荐事件</div>}
                </div>
            </div>
        </div>
    );
}

function DailyStrategyReportCard({ data }: { data: DailyStrategyReport | null }) {
    const summary = data?.summary || {};
    const gaps = data?.sector_push_gaps || [];
    const actions = data?.next_actions || [];
    const candidates = data?.top_candidates || [];

    return (
        <div className="glass-card p-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <CalendarDays size={18} className="text-indigo-600" />
                        <h3 className="font-black text-slate-800">收盘策略日报</h3>
                        <span className="rounded-md border border-slate-100 bg-slate-50 px-2 py-0.5 text-[10px] font-black text-slate-400">
                            {data?.scan_date || '--'}
                        </span>
                    </div>
                    <p className="mt-2 text-sm font-bold text-slate-600">{summary.stance || '暂无收盘日报'}</p>
                </div>
                <div className="grid grid-cols-5 gap-2 min-w-full lg:min-w-[520px]">
                    <MiniStat label="扫描" value={`${summary.scan_count || 0}`} />
                    <MiniStat label="TRADE" value={`${summary.trade_count || 0}`} hot={(summary.trade_count || 0) > 0} />
                    <MiniStat label="EARLY" value={`${summary.early_count || 0}`} hot={(summary.early_count || 0) > 0} />
                    <MiniStat label="观察" value={`${summary.observe_count || 0}`} />
                    <MiniStat label="Bark" value={`${summary.bark_push_count || 0}`} hot={(summary.bark_push_count || 0) > 0} />
                </div>
            </div>

            <div className="mt-4 grid grid-cols-1 xl:grid-cols-3 gap-3">
                <div className="rounded-md border border-slate-100 bg-white/70 p-3">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">热门板块未推</div>
                    <div className="space-y-2">
                        {gaps.length === 0 ? (
                            <p className="text-xs font-bold text-slate-400">暂无热门板块缺口</p>
                        ) : gaps.slice(0, 4).map((item, idx) => (
                            <div key={`${item.industry}-${idx}`} className="border-b border-slate-50 pb-2 last:border-b-0 last:pb-0">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-sm font-black text-slate-700">{item.industry || '--'}</span>
                                    <span className="text-[10px] font-black text-slate-400">板块分 {item.sector_momentum_score ?? '--'}</span>
                                </div>
                                <p className="mt-1 text-xs font-bold text-slate-500">{item.primary_reason_label || '继续观察'} · 候选 {item.scan_candidate_count ?? 0}只</p>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="rounded-md border border-slate-100 bg-white/70 p-3">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">明日动作</div>
                    <div className="space-y-2">
                        {actions.length === 0 ? (
                            <p className="text-xs font-bold text-slate-400">暂无动作建议</p>
                        ) : actions.slice(0, 4).map((action, idx) => (
                            <p key={`${action}-${idx}`} className="text-xs font-bold leading-relaxed text-slate-600">
                                {idx + 1}. {action}
                            </p>
                        ))}
                    </div>
                </div>

                <div className="rounded-md border border-slate-100 bg-white/70 p-3">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">高分候选</div>
                    <div className="space-y-2">
                        {candidates.length === 0 ? (
                            <p className="text-xs font-bold text-slate-400">暂无候选</p>
                        ) : candidates.slice(0, 4).map((item, idx) => (
                            <div key={`${item.code}-${idx}`} className="flex items-center justify-between gap-3 border-b border-slate-50 pb-2 last:border-b-0 last:pb-0">
                                <div className="min-w-0">
                                    <p className="truncate text-sm font-black text-slate-700">{item.name || item.code}</p>
                                    <p className="text-[10px] font-bold text-slate-400">{item.industry || '--'} · {item.bucket || '--'} · {item.grade || '--'}</p>
                                </div>
                                <span className="text-sm font-black text-indigo-600">{item.score ?? '--'}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}

function ProfitabilityLayerCard({ data }: { data: ProfitabilityPayload | null }) {
    const layers = data?.layers || [];
    const summary = data?.summary || {};
    const priority = ['bark', 'strong_sector', 'tv_dual_strict', 'early_a_minus', 'trade_a', 'real_trade', 'scan_all'];
    const rows = [...layers].sort((a, b) => {
        const ai = priority.indexOf(a.key);
        const bi = priority.indexOf(b.key);
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });

    return (
        <div className="glass-card p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between mb-5">
                <div>
                    <h3 className="font-black text-slate-800">盈利能力分层</h3>
                    <p className="text-xs font-bold text-slate-400 mt-1">按扫描、Bark、A/A-、实盘买入分别验证 1/3/5/10 日收益</p>
                </div>
                <div className="grid grid-cols-3 gap-2 min-w-[260px]">
                    <MiniStat label="最佳层" value={summary.best_layer || '样本不足'} hot />
                    <MiniStat label="正期望层" value={`${summary.positive_layers || 0}`} hot={(summary.positive_layers || 0) > 0} />
                    <MiniStat label="弱势层" value={`${summary.weak_layers || 0}`} />
                </div>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-left">
                    <thead>
                        <tr className="text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
                            <th className="py-2 pr-3">分层</th>
                            <th className="py-2 pr-3">样本</th>
                            <th className="py-2 pr-3">1日</th>
                            <th className="py-2 pr-3">3日</th>
                            <th className="py-2 pr-3">5日</th>
                            <th className="py-2 pr-3">10日</th>
                            <th className="py-2 pr-3">状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((layer: ProfitabilityLayer) => {
                            const m1 = layer.metrics?.['1d'] || {};
                            const m3 = layer.metrics?.['3d'] || {};
                            const m5 = layer.metrics?.['5d'] || {};
                            const m10 = layer.metrics?.['10d'] || {};
                            return (
                                <tr key={layer.key} className="border-b border-slate-50 last:border-b-0 text-xs">
                                    <td className="py-3 pr-3">
                                        <div className="font-black text-slate-800">{layer.label}</div>
                                        <div className="text-[10px] font-bold text-slate-400">{layer.source}</div>
                                    </td>
                                    <td className="py-3 pr-3 font-black text-slate-700">{m5.signals || 0}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m1)}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m3)}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m5)}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m10)}</td>
                                    <td className="py-3 pr-3 min-w-[150px]">
                                        <span className={cn("px-2 py-1 rounded-md border text-[10px] font-black", verdictTone(layer.verdict?.status))}>
                                            {layer.verdict?.status || 'UNKNOWN'}
                                        </span>
                                        <div className="mt-1 text-[10px] font-bold text-slate-400">{layer.verdict?.action || '--'}</div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {rows.length === 0 && <div className="py-12 text-center text-slate-400 font-bold">暂无盈利能力分层数据</div>}
            </div>
        </div>
    );
}

function ExecutionReviewCard({ data, contract }: { data?: ExecutionReview; contract?: MeasurementContract }) {
    const diagnostics = data?.diagnostics || [];
    return (
        <div className="glass-card p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h3 className="font-black text-slate-800">真实执行差距</h3>
                    <p className="mt-1 text-xs font-bold text-slate-400">区分系统信号表现与实际买入执行质量</p>
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:min-w-[620px]">
                    <MiniStat label="执行质量" value={`${data?.execution_quality_score ?? 0}`} hot={(data?.execution_quality_score ?? 0) >= 80} />
                    <MiniStat label="实盘样本" value={`${data?.sample || 0}`} />
                    <MiniStat label="5日执行差" value={formatSignedPct(data?.execution_gap_5d)} hot={(data?.execution_gap_5d ?? -1) >= 0} />
                    <MiniStat label="平均滑点" value={formatSignedPct(data?.avg_entry_slippage_pct)} />
                </div>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
                <div className="rounded-md border border-slate-100 bg-slate-50/70 px-3 py-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">字段完整度</div>
                    <div className="mt-2 space-y-1 text-xs font-bold text-slate-600">
                        <p>计划遵守未知：{data?.unknown_plan_adherence || 0}笔</p>
                        <p>缺少计划价：{data?.missing_planned_entry || 0}笔</p>
                        <p>缺少信号日期：{data?.missing_signal_date || 0}笔</p>
                    </div>
                </div>
                <div className="rounded-md border border-slate-100 bg-white/70 px-3 py-3 lg:col-span-2">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">执行诊断</div>
                    <div className="mt-2 space-y-1 text-xs font-bold text-slate-600">
                        {diagnostics.length ? diagnostics.slice(0, 4).map((item, idx) => <p key={`${item}-${idx}`}>{idx + 1}. {item}</p>) : <p>暂无可归因的真实交易样本</p>}
                    </div>
                </div>
            </div>
            <MeasurementContractLine contract={contract} />
        </div>
    );
}

function CalibrationCard({ data }: { data: CalibrationPayload | null }) {
    const monotonicity = data?.grade_monotonicity || {};
    const confirmationRows = (data?.by_confirmation_event || []).filter(row => row.value !== 'UNKNOWN');
    const earlyRows = (data?.by_early_value_transition || []).filter(row => row.value !== 'UNKNOWN');
    const reviewRules = (data?.blocker_analysis?.items || []).filter(row => row.recommendation === 'REVIEW_RULE');
    const bottom = data?.bottom_discovery_analysis;
    const sampleText = Object.entries(monotonicity.samples || {}).map(([grade, count]) => `${grade}:${count}`).join(' / ');
    return (
        <div className="glass-card p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h3 className="font-black text-slate-800">评级与确认校准</h3>
                    <p className="mt-1 text-xs font-bold text-slate-400">只展示成熟样本结论，样本不足时不触发调参</p>
                </div>
                <div className="grid grid-cols-3 gap-2 lg:min-w-[430px]">
                    <MiniStat label="5日成熟" value={`${data?.summary?.mature_5d || 0}`} />
                    <MiniStat label="Grade单调" value={monotonicity.status || 'UNKNOWN'} hot={monotonicity.status === 'PASS'} />
                    <MiniStat label="待复核规则" value={`${data?.blocker_analysis?.summary?.review_rules || 0}`} />
                </div>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-4">
                <CalibrationRows title="确认事件" rows={confirmationRows} empty="新事件尚未形成成熟样本" />
                <CalibrationRows title="early_value 转化" rows={earlyRows} empty="早期策略尚未形成可验证转化" />
                <BottomDiscoveryCalibration data={bottom} />
                <div className="rounded-md border border-slate-100 bg-white/70 p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Blocker 复核</div>
                    <div className="mt-2 space-y-2">
                        {reviewRules.slice(0, 4).map((row, idx) => {
                            const controlled = row.controlled_comparison?.hit_minus_miss_avg_return;
                            return (
                                <div key={`${row.blocker}-${idx}`} className="border-b border-slate-50 pb-2 last:border-b-0">
                                    <p className="text-xs font-black text-slate-700">{row.blocker}</p>
                                    <p className="mt-0.5 text-[10px] font-bold text-slate-400">
                                        命中 {row.occurrences || 0} · 控制后差异 {formatSignedPct(controlled)} · 配对 {row.controlled_comparison?.paired_samples || 0}
                                    </p>
                                </div>
                            );
                        })}
                        {!reviewRules.length && <p className="text-xs font-bold text-slate-400">暂无需要复核的成熟规则</p>}
                    </div>
                </div>
            </div>
            <div className="mt-3 text-[10px] font-bold text-slate-400">Grade成熟样本：{sampleText || '暂无'}；{monotonicity.reason || '等待样本成熟'}</div>
            <MeasurementContractLine contract={data?.measurement_contract} />
        </div>
    );
}

function BottomDiscoveryCalibration({ data }: { data?: CalibrationPayload['bottom_discovery_analysis'] }) {
    const conversion = data?.conversion || {};
    const confirmation = data?.formal_confirmation || {};
    const strictLead = data?.strict_strategy_lead || {};
    const rows = data?.by_stage || [];
    return (
        <div className="rounded-md border border-slate-100 bg-white/70 p-3">
            <div className="flex items-center justify-between gap-2">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">底部起涨验证</div>
                <span className={cn(
                    "rounded-full px-2 py-0.5 text-[9px] font-black",
                    data?.status === 'VALIDATED' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                )}>{data?.status || 'INSUFFICIENT_DATA'}</span>
            </div>
            <div className="mt-2 space-y-2">
                {rows.map(row => (
                    <div key={row.value} className="border-b border-slate-50 pb-2 last:border-b-0">
                        <div className="flex items-center justify-between gap-2">
                            <p className="text-xs font-black text-slate-700">{row.value}</p>
                            <p className="text-[10px] font-bold text-slate-500">5日 {formatLayerMetric(row.metrics?.['5d'])}</p>
                        </div>
                        <p className="mt-0.5 text-[10px] font-bold text-slate-400">
                            成熟 {row.mature_5d || 0}/{row.signals || 0} · MFE {formatSignedPct(row.excursion_5d?.avg_mfe)} · MAE {formatSignedPct(row.excursion_5d?.avg_mae)}
                        </p>
                    </div>
                ))}
                {!rows.length && <p className="text-xs font-bold text-slate-400">等待 B0/B1 信号形成成熟样本</p>}
            </div>
            <p className="mt-2 text-[10px] font-bold text-slate-400">
                B0→B1 {conversion.converted_to_b1 || 0}/{conversion.b0_unique_stocks || 0}
                {conversion.conversion_rate != null ? `（${conversion.conversion_rate}%）` : ''}
                {conversion.median_wait_calendar_days != null ? ` · 中位${conversion.median_wait_calendar_days}天` : ''}
            </p>
            <p className="mt-1 text-[10px] font-bold text-slate-400">
                B1→可交易 {confirmation.confirmed_stocks || 0}/{confirmation.mature_b1_followups || 0}个成熟窗口
                {confirmation.confirmation_rate != null ? `（${confirmation.confirmation_rate}%）` : ''}
                {confirmation.median_wait_calendar_days != null ? ` · 中位${confirmation.median_wait_calendar_days}天` : ''}
            </p>
            <p className="mt-1 text-[10px] font-bold text-slate-400">
                领先严格策略 {strictLead.matched_stocks || 0}只
                {strictLead.match_rate != null ? `（覆盖${strictLead.match_rate}%）` : ''}
                {strictLead.median_lead_calendar_days != null ? ` · 中位${strictLead.median_lead_calendar_days}天` : ''}
            </p>
        </div>
    );
}

function CalibrationRows({ title, rows, empty }: { title: string; rows: CalibrationRow[]; empty: string }) {
    return (
        <div className="rounded-md border border-slate-100 bg-white/70 p-3">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{title}</div>
            <div className="mt-2 space-y-2">
                {rows.slice(0, 5).map(row => (
                    <div key={row.value} className="flex items-center justify-between gap-3 border-b border-slate-50 pb-2 last:border-b-0">
                        <div>
                            <p className="text-xs font-black text-slate-700">{row.value}</p>
                            <p className="text-[10px] font-bold text-slate-400">{row.mature_5d || 0}/{row.signals || 0} 个5日成熟</p>
                        </div>
                        <div className="text-right text-[10px] font-bold text-slate-500">{formatLayerMetric(row.metrics?.['5d'])}</div>
                    </div>
                ))}
                {!rows.length && <p className="text-xs font-bold text-slate-400">{empty}</p>}
            </div>
        </div>
    );
}

function MeasurementContractLine({ contract }: { contract?: MeasurementContract }) {
    if (!contract) return null;
    return (
        <div className="mt-3 rounded-md border border-slate-100 bg-slate-50/60 px-3 py-2 text-[10px] font-bold text-slate-400">
            口径：{contract.sample_unit || '--'}；入场价：{contract.entry_price || '--'}；成熟规则：{contract.maturity_rule || '--'}；{contract.benchmark_adjusted ? '含基准超额收益' : '当前为绝对收益'}
        </div>
    );
}

function SignalPerformanceCard({ data }: { data: SignalPerformancePayload | null }) {
    if (!data) return null;
    const summary = data.summary || {};
    const coverage = data.strong_stock_coverage || {};
    const attribution = data.gate_attribution || {};
    const validation = data.validation || {};
    const cohort = (name: string) => (data.cohorts || []).find((item) => item.cohort === name) || {};
    const selection = cohort('selection');
    const execution = cohort('execution');
    const blocked = cohort('blocked');
    const shadow = cohort('strong_exception_shadow');
    const ratingExplanations = (data.items || []).filter((item) => item.sop_grade_reason).slice(-3).reverse();
    const returnText = (value?: SignalMetric) => {
        if (!value?.signals) return '样本未成熟';
        const average = value.avg_return ?? 0;
        return `${average >= 0 ? '+' : ''}${average}% / 胜率${value.win_rate ?? 0}%`;
    };
    const cohortCards: Array<[string, SignalPerformanceCohort]> = [
        ['选股候选', selection], ['Bark可交易', execution],
        ['门禁拦截', blocked], ['强势例外SHADOW', shadow],
    ];
    return (
        <div className="glass-card p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h3 className="font-black text-slate-800">盘中信号点时表现</h3>
                    <p className="mt-1 text-xs font-bold text-slate-400">选股、提醒时机、确认触发和门禁反事实分开统计</p>
                </div>
                <span className={cn(
                    "rounded-full px-2.5 py-1 text-[10px] font-black",
                    data.status === 'VALIDATED' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                )}>{data.status || 'INSUFFICIENT_DATA'}</span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
                <MiniStat label="不可变快照" value={`${summary.snapshot_events || 0}`} />
                <MiniStat label="去重候选" value={`${summary.unique_daily_candidates || 0}`} />
                <MiniStat label="可交易样本" value={`${summary.tradable_candidates || 0}`} />
                <MiniStat label="强势例外影子" value={`${summary.strong_exception_shadow || 0}`} />
                <MiniStat label="5日成熟" value={`${summary.mature_5d || 0}`} />
            </div>
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
                {[
                    ['选股有效性', validation.selection, 'mature_5d'],
                    ['可交易判断', validation.execution, 'mature_5d'],
                    ['确认触发', validation.confirmation, 'triggered_samples'],
                ].map(([label, item, countKey]) => {
                    const state = item as { status?: string; mature_5d?: number; triggered_samples?: number; required?: number } | undefined;
                    const count = state?.[countKey as 'mature_5d' | 'triggered_samples'] || 0;
                    const valid = state?.status === 'VALIDATED';
                    return (
                        <div key={label as string} className="rounded-xl border border-slate-100 bg-white p-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-black text-slate-700">{label as string}</span>
                                <span className={cn(
                                    "rounded-full px-2 py-0.5 text-[10px] font-black",
                                    valid ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                                )}>{valid ? '样本已成熟' : '样本不足'}</span>
                            </div>
                            <div className="mt-1 text-[11px] font-bold text-slate-400">{count}/{state?.required || 30}</div>
                        </div>
                    );
                })}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                {cohortCards.map(([label, item]) => (
                    <div key={label} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{label}</div>
                        <div className="mt-2 text-sm font-black text-slate-700">提醒→收盘 {returnText(item.alert_to_close)}</div>
                        <div className="mt-1 text-xs font-bold text-slate-500">5日 {returnText(item.ret_5d)}</div>
                    </div>
                ))}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
                <div className="rounded-xl border border-slate-100 bg-white p-3 text-xs font-bold text-slate-600">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">强股覆盖</div>
                    <div className="grid grid-cols-3 gap-2">
                        <span>Top20 {coverage.top20_hits || 0}（{coverage.top20_coverage_pct || 0}%）</span>
                        <span>Top50 {coverage.top50_hits || 0}（{coverage.top50_coverage_pct || 0}%）</span>
                        <span>Top100 {coverage.top100_hits || 0}（{coverage.top100_coverage_pct || 0}%）</span>
                    </div>
                </div>
                <div className="rounded-xl border border-slate-100 bg-white p-3 text-xs font-bold text-slate-600">
                    <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-400">门禁归因</div>
                    <div className="grid grid-cols-2 gap-2">
                        <span>避免亏损 {attribution.RISK_GATE_SAVED_LOSS || 0}</span>
                        <span>漏掉赢家 {attribution.RISK_GATE_MISSED_WINNER || 0}</span>
                        <span>确认触发 {attribution.CONFIRMATION_TRIGGERED || 0}</span>
                        <span>结果未成熟 {attribution.NO_5D_OUTCOME || 0}</span>
                    </div>
                </div>
            </div>
            {ratingExplanations.length > 0 && (
                <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">最近评级解释</div>
                    <div className="mt-2 space-y-2">
                        {ratingExplanations.map((item) => (
                            <div key={item.signal_id || `${item.code}-${item.grade}`} className="text-xs font-bold text-slate-600">
                                <span className="font-black text-slate-800">{item.name || item.code}：</span>
                                基础{item.sop_base_grade || item.grade || '--'}→最终{item.grade || '--'}；质量分{Math.max(0, Math.min(100, item.display_quality_score ?? item.sop_quality_score ?? 0)).toFixed(1)}/100；{item.sop_grade_reason}
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {data.note && <p className="mt-3 text-xs font-bold text-slate-400">{data.note}</p>}
        </div>
    );
}


function ExecutionReplayCard({ data }: { data: any }) {
    if (!data) return null;
    const statusTone = data.verdict === 'SUPPORTED' ? 'text-emerald-600' : data.verdict === 'NOT_SUPPORTED' ? 'text-rose-600' : 'text-amber-600';
    const policies = Array.isArray(data.policies) ? data.policies : [];
    const evidence = data.evidence_quality || {};
    const grades = evidence.grade_distribution || {};
    const attribution = evidence.attribution || {};
    const operation = data.operation_advice_validation || {};
    const barkEvidence = operation.actual_bark_evidence || {};
    const actualFillEvidence = barkEvidence.actual_fill_evidence || {};
    return (
        <div className="glass-card p-5">
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h3 className="font-black text-slate-800">执行策略历史验证</h3>
                    <p className="text-xs text-slate-400 mt-1">点时候选重放，未来收益只用于评价</p>
                </div>
                <span className={cn("text-sm font-black", statusTone)}>{data.verdict || 'UNKNOWN'}</span>
            </div>
            <p className="text-xs font-bold text-slate-600 mb-4">{data.verdict_reason || '暂无验证结论'}</p>
            <div className={cn(
                "mb-4 rounded-xl border p-3",
                barkEvidence.status === 'VALIDATED' ? 'border-emerald-100 bg-emerald-50/60' : 'border-amber-100 bg-amber-50/60'
            )}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div className="text-xs font-black text-slate-800">真实 Bark 指令证据</div>
                        <div className="mt-1 text-[10px] font-bold text-slate-500">研究候选与已发送“可交易”指令分开验收</div>
                    </div>
                    <span className="text-[10px] font-black text-amber-700">{barkEvidence.status || 'INSUFFICIENT_DATA'}</span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-5">
                    <MiniStat label="生成可交易" value={`${barkEvidence.tradable_instructions || 0}`} />
                    <MiniStat label="已发送审计" value={`${barkEvidence.audited_delivered_instructions || 0}`} hot={(barkEvidence.audited_delivered_instructions || 0) > 0} />
                    <MiniStat label="记录真实成交" value={`${actualFillEvidence.fills || 0}`} hot={(actualFillEvidence.fills || 0) > 0} />
                    <MiniStat label="真实成交成熟" value={`${actualFillEvidence.mature || 0}`} />
                    <MiniStat label="验收门槛" value={`${barkEvidence.required || 30}`} />
                </div>
                <p className="mt-2 text-[10px] font-bold text-slate-500">{barkEvidence.note || '等待真实指令样本积累'}</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                {policies.map((item: any) => (
                    <div key={item.policy} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                        <div className="text-[10px] font-black text-slate-400 uppercase">{item.policy}</div>
                        <div className="mt-2 text-sm font-black text-slate-700">成熟 {item.metrics_5d?.signals || 0} 笔</div>
                        <div className="mt-1 text-xs text-slate-500">胜率 {item.metrics_5d?.win_rate || 0}%｜均收 {item.metrics_5d?.avg_return || 0}%</div>
                    </div>
                ))}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
                <div className="rounded-xl border border-slate-100 bg-white p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">证据质量分布 · {evidence.mode || 'SHADOW'}</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                        {['A', 'B', 'C', 'D', 'F', 'UNRATED'].map(grade => (
                            <span key={grade} className="rounded-md border border-slate-100 bg-slate-50 px-2 py-1 text-xs font-black text-slate-600">
                                {grade}: {grades[grade] || 0}
                            </span>
                        ))}
                    </div>
                </div>
                <div className="rounded-xl border border-slate-100 bg-white p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">影子门禁归因</div>
                    <div className="mt-2 grid grid-cols-2 gap-2 text-xs font-bold text-slate-600">
                        <span>避免亏损 {attribution.RISK_GATE_SAVED_LOSS || 0}</span>
                        <span>漏掉赢家 {attribution.RISK_GATE_MISSED_WINNER || 0}</span>
                        <span>数据缺失 {attribution.DATA_MISSING || 0}</span>
                        <span>未成交 {attribution.NO_FILL || 0}</span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function SectorWatchPerformanceCard({ data }: { data: SectorWatchPerformance | null }) {
    const rows = data?.by_state || [];
    const summary = data?.summary || {};
    const sourceLabel = data?.source === 'state_events' ? '状态事件日志' : '当前状态画像';
    const suggestions = data?.suggestions || [];
    const notes = data?.notes || [];

    return (
        <div className="glass-card p-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between mb-5">
                <div>
                    <h3 className="font-black text-slate-800">题材状态复盘</h3>
                    <p className="text-xs font-bold text-slate-400 mt-1">验证接近确认价、等回踩、回踩放量确认等状态后的表现</p>
                </div>
                <div className="grid grid-cols-3 gap-2 min-w-[300px]">
                    <MiniStat label="样本" value={`${summary.items || 0}`} />
                    <MiniStat label="5日成熟" value={`${summary.mature_5d || 0}`} />
                    <MiniStat label="最佳状态" value={summary.best_state || '样本不足'} hot />
                </div>
            </div>

            <div className="mb-4 grid grid-cols-1 lg:grid-cols-3 gap-3">
                <div className="rounded-md border border-slate-100 bg-slate-50/70 px-3 py-2">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">统计来源</div>
                    <div className="mt-1 text-xs font-black text-slate-700">{sourceLabel}</div>
                </div>
                <div className="lg:col-span-2 rounded-md border border-slate-100 bg-white/70 px-3 py-2">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">调权建议</div>
                    <div className="mt-1 text-xs font-bold text-slate-600">
                        {suggestions.length > 0 ? suggestions.slice(0, 2).join('；') : notes[0] || '样本积累后用于调整 Bark 优先级'}
                    </div>
                </div>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-left">
                    <thead>
                        <tr className="text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
                            <th className="py-2 pr-3">状态</th>
                            <th className="py-2 pr-3">样本</th>
                            <th className="py-2 pr-3">5日</th>
                            <th className="py-2 pr-3">1日</th>
                            <th className="py-2 pr-3">3日</th>
                            <th className="py-2 pr-3">10日</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => {
                            const m1 = row.metrics?.['1d'] || {};
                            const m3 = row.metrics?.['3d'] || {};
                            const m5 = row.metrics?.['5d'] || {};
                            const m10 = row.metrics?.['10d'] || {};
                            return (
                                <tr key={row.state || row.label} className="border-b border-slate-50 last:border-b-0 text-xs">
                                    <td className="py-3 pr-3 min-w-[150px]">
                                        <div className="font-black text-slate-800">{row.label || row.state || '--'}</div>
                                        <div className="text-[10px] font-bold text-slate-400">{row.state || 'UNKNOWN'}</div>
                                    </td>
                                    <td className="py-3 pr-3">
                                        <div className="font-black text-slate-700">{row.items || 0}</div>
                                        <div className="text-[10px] font-bold text-slate-400">{row.mature_5d || 0} 个5日成熟</div>
                                    </td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m5)}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m1)}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m3)}</td>
                                    <td className="py-3 pr-3">{formatLayerMetric(m10)}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {rows.length === 0 && <div className="py-12 text-center text-slate-400 font-bold">暂无题材状态复盘样本</div>}
            </div>
        </div>
    );
}

function verdictTone(status?: string) {
    if (status === 'POSITIVE') return 'bg-rose-50 text-rose-600 border-rose-100';
    if (status === 'WATCH') return 'bg-indigo-50 text-indigo-600 border-indigo-100';
    if (status === 'WEAK') return 'bg-slate-100 text-slate-700 border-slate-200';
    return 'bg-amber-50 text-amber-700 border-amber-100';
}

function formatLayerMetric(metric?: LayerMetric) {
    const value = Number(metric?.avg_return || 0);
    const signals = Number(metric?.signals || 0);
    const winRate = Number(metric?.win_rate || 0);
    const color = value >= 0 ? 'text-rose-600' : 'text-emerald-600';
    return (
        <div>
            <div className={cn("font-black", color)}>{signals ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '--'}</div>
            <div className="text-[10px] font-bold text-slate-400">{signals ? `${winRate}%胜` : '无样本'}</div>
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

function adjustmentLabel(action?: string) {
    if (action === 'BOOST') return '加权';
    if (action === 'DOWNWEIGHT') return '降权';
    if (action === 'OBSERVE') return '观察';
    return '保持';
}

function adjustmentTone(action?: string) {
    if (action === 'BOOST') return 'border-rose-100 bg-rose-50 text-rose-600';
    if (action === 'DOWNWEIGHT') return 'border-emerald-100 bg-emerald-50 text-emerald-700';
    if (action === 'OBSERVE') return 'border-amber-100 bg-amber-50 text-amber-700';
    return 'border-slate-100 bg-slate-50 text-slate-500';
}

function formatScoreDelta(value?: number) {
    if (!value) return '';
    return `${value > 0 ? '+' : ''}${value}`;
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
