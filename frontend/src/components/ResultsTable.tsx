"use client";

import React, { useEffect, useMemo, useState } from 'react';
import {
    Target,
    Map as MapIcon,
    BarChart3,
    History,
    Layers,
    ArrowUpDown,
    ExternalLink,
    HelpCircle,
    ChevronDown,
    ChevronUp,
    AlertTriangle,
    Lock,
    Calendar,
    Plus,
    Calculator as CalcIcon,
    Settings2,
    Eye,
    Loader2,
    Sparkles
} from 'lucide-react';
import { clampScore, cn } from '@/lib/utils';
import { downloadCsv, toCsvString } from '@/lib/csv';
import api from '@/lib/api';
import dynamic from 'next/dynamic';
const StockChart = dynamic(() => import('./StockChart'), { ssr: false, loading: () => <div className="h-48 flex items-center justify-center text-slate-400 text-xs">Loading chart...</div> });
import PositionSizer from './PositionSizer';
import HeatmapOptimizer from './HeatmapOptimizer';
import ModalOverlay from './ui/ModalOverlay';
import { ScanResult } from '@/stores/scanStore';

type AIReview = {
    code: string;
    name: string;
    action: 'BUY' | 'WAIT' | 'AVOID';
    confidence: number;
    summary: string;
    positive_factors: string[];
    risk_factors: string[];
    data_limitations: string[];
    guardrail_adjusted?: boolean;
    system_levels?: { entry_price?: number; stop_price?: number; target_price?: number };
};
type AIPerformance = {
    status: 'available' | 'none';
    horizons?: {
        '5d'?: {
            all_reviewed?: { signals: number; win_rate: number; avg_return: number };
            buy?: { signals: number; win_rate: number; avg_return: number };
            buy_lift?: { win_rate_pct_points?: number | null; avg_return_pct_points?: number | null };
        };
    };
    sample_warning?: string | null;
};
type ExecutionTimeline = {
    code: string;
    items: Array<{
        scanned_at: string;
        data_date: string;
        strategy_type: string;
        trade_bucket?: string;
        price?: number;
        confirmation_price?: number;
        stop_price?: number;
        target_price?: number;
        plan_state?: { state?: string };
        lifecycle?: string;
        blockers?: string[];
    }>;
};
export default function ResultsTable({
    results,
    onSelectStock,
    selectedCode
}: {
    results: ScanResult[],
    onSelectStock?: (stock: ScanResult) => void,
    selectedCode?: string
}) {
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [sizingStock, setSizingStock] = useState<ScanResult | null>(null);
    const [optimizingStock, setOptimizingStock] = useState<ScanResult | null>(null);
    const [remarkStock, setRemarkStock] = useState<ScanResult | null>(null);
    const [remarkText, setRemarkText] = useState('');
    const [addTradeMode, setAddTradeMode] = useState<'SIMULATED' | 'REAL'>('SIMULATED');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [groupBySector, setGroupBySector] = useState(false);
    const [sectorSortKey, setSectorSortKey] = useState<'count' | 'avgScore'>('count');
    const [collapsedSectors, setCollapsedSectors] = useState<Set<string>>(new Set());
    const [aiReviews, setAIReviews] = useState<Record<string, AIReview>>({});
    const [aiSummary, setAISummary] = useState('');
    const [aiLoading, setAILoading] = useState(false);
    const [aiPerformance, setAIPerformance] = useState<AIPerformance | null>(null);
    const [brooksFilter, setBrooksFilter] = useState<'ALL' | 'READY' | 'NO_AVOID' | 'LOW_RISK' | 'PULLBACK' | 'LOW_FAILURE' | 'H2_STRONG' | 'STRONG_TREND'>('ALL');
    const [timeline, setTimeline] = useState<ExecutionTimeline | null>(null);
    const [timelineLoading, setTimelineLoading] = useState(false);

    const openTimeline = async (code: string) => {
        setTimelineLoading(true);
        try {
            const response = await api.get(`/api/review/execution-plan-timeline/${code}`);
            setTimeline(response.data);
        } catch {
            showToast('时间线加载失败', 'error');
        } finally {
            setTimelineLoading(false);
        }
    };

    const applyBrooksFilter = (rows: ScanResult[]) => {
        if (brooksFilter === 'READY') {
            return rows.filter(r => r.pa_trade_plan?.action === 'READY' || r.pa_trade_action === 'READY');
        }
        if (brooksFilter === 'NO_AVOID') {
            return rows.filter(r => (r.pa_trade_plan?.action || r.pa_trade_action) !== 'AVOID');
        }
        if (brooksFilter === 'LOW_RISK') {
            return rows.filter(r => {
                const risk = r.pa_trade_plan?.risk_pct ?? r.pa_risk_pct ?? 999;
                return risk > 0 && risk <= 8;
            });
        }
        if (brooksFilter === 'LOW_FAILURE') {
            return rows.filter(r => (r.pa_failure_risk ?? 100) <= 55);
        }
        if (brooksFilter === 'H2_STRONG') {
            return rows.filter(r => r.pa_h2_quality === '强' || (r.pa_entry_quality_score ?? 0) >= 75);
        }
        if (brooksFilter === 'STRONG_TREND') {
            return rows.filter(r => (r.pa_always_in_strength ?? 0) >= 70 && r.pa_trend_damage !== '跌破EMA20' && r.pa_trend_damage !== '跌破EMA60');
        }
        if (brooksFilter === 'PULLBACK') {
            return rows.filter(r => {
                const setup = r.pa_trade_plan?.setup || r.pa_trade_setup || r.price_action_pattern || '';
                return setup.includes('H2') || setup.includes('回踩') || r.pa_pullback_structure === '双腿回调';
            });
        }
        return rows;
    };

    const filteredResults = applyBrooksFilter(results);
    const aiFiveDayPerformance = aiPerformance?.horizons?.['5d'];

    useEffect(() => {
        setAIReviews({});
        setAISummary('');
        setAIPerformance(null);
    }, [results]);

    // ── Sector grouping logic ──
    const sectorGroups = useMemo(() => {
        if (!groupBySector) return null;
        const map = new Map<string, ScanResult[]>();
        filteredResults.forEach(r => {
            const sector = (r.行业 && r.行业.trim()) || '未分类';
            if (!map.has(sector)) map.set(sector, []);
            map.get(sector)!.push(r);
        });
        // Sort stocks within each group by Score descending
        map.forEach(stocks => stocks.sort((a, b) => b.Score - a.Score));
        // Sort sector groups
        const entries = Array.from(map.entries());
        if (sectorSortKey === 'count') {
            entries.sort((a, b) => b[1].length - a[1].length);
        } else {
            entries.sort((a, b) => {
                const avgA = a[1].reduce((s, r) => s + r.Score, 0) / a[1].length;
                const avgB = b[1].reduce((s, r) => s + r.Score, 0) / b[1].length;
                return avgB - avgA;
            });
        }
        return entries;
    }, [filteredResults, groupBySector, sectorSortKey]);

    const toggleSectorCollapse = (sector: string) => {
        setCollapsedSectors(prev => {
            const next = new Set(prev);
            if (next.has(sector)) next.delete(sector);
            else next.add(sector);
            return next;
        });
    };

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 2500);
    };

    if (results.length === 0) return null;

    const explainBrooksAction = (stock: ScanResult) => {
        const action = stock.pa_trade_plan?.action || stock.pa_trade_action || 'WAIT';
        if (action === 'READY') return '条件接近成熟，可等待触发价确认后按计划执行。';
        if (action === 'WATCH') return '结构值得跟踪，但不适合直接追价，先放入观察池。';
        if (action === 'AVOID') return '结构或位置不佳，暂时回避主动买入。';
        return '方向尚未确认，等待下一根K线或尾盘定型。';
    };

    const explainBrooksScore = (score?: number) => {
        if (!score) return '暂无评分';
        if (score >= 72) return '高质量结构';
        if (score >= 55) return '可观察结构';
        if (score >= 40) return '低把握结构';
        return '观望结构';
    };

    const explainRiskReward = (rr?: number) => {
        if (!rr) return '空间未确认';
        if (rr >= 2) return '风险收益较优';
        if (rr >= 1.5) return '风险收益可接受';
        return '风险收益偏低';
    };

    const explainFailureRisk = (risk?: number) => {
        if (risk == null) return '--';
        if (risk >= 70) return `${risk}% 偏高`;
        if (risk >= 45) return `${risk}% 中等`;
        return `${risk}% 较低`;
    };

    const explainH2Quality = (quality?: string, score?: number) => {
        if (!quality || quality === '不适用') return score ? `${score}分` : '--';
        return score ? `${quality} · ${score}分` : quality;
    };

    const getRowKey = (res: ScanResult, index: number) => [
        res.代码,
        res.strategy_type || 'strategy',
        res.date || res.日期 || 'latest',
        res.行业 || 'sector',
        index,
    ].join('-');

    const toggleRow = (rowKey: string) => {
        setExpandedRow(expandedRow === rowKey ? null : rowKey);
    };

    const openChart = (code: string) => {
        const fullCode = code.startsWith('6') || code.startsWith('688') ? `SH${code}` : `SZ${code}`;
        window.open(`https://quote.eastmoney.com/${fullCode}.html`, '_blank');
    };

    const handleExport = () => {
        const headers = ['代码', '名称', '行业', '现价', '涨幅%', '信号强度(0-100)', '结构质量(0-100)', '机会分(0-100)', 'AI建议', 'AI置信度', 'AI结论', 'RSI', 'DIF', 'BB', '粘合度', 'ROE', '净利YOY', '历史胜率', '信号次数', '北向', '共振', 'TV均线', 'TV-ZP', 'TV命中', '影线比', 'strategy_type', 'matched_strategies'];
        const rows = results.map(r => [
            r.代码, r.名称, r.行业, r.现价, r['涨幅%'], clampScore(r.display_signal_score ?? r.Score),
            r.sop_quality_score == null ? '' : clampScore(r.display_quality_score ?? r.sop_quality_score),
            r.trade_opportunity_score == null ? '' : clampScore(r.display_opportunity_score ?? r.trade_opportunity_score),
            aiReviews[r.代码]?.action || '', aiReviews[r.代码]?.confidence ?? '', aiReviews[r.代码]?.summary || '', r.RSI, r.DIF, r.BB,
            r.粘合度, r.ROE || '', r.净利YOY || '', r.历史胜率, r.信号次数, r.北向 || '', r.共振 || '',
            r.tv_ma_signal || '', r.tv_zp_signal || '', r.tv_match || '',
            r.影线比 || '', r.strategy_type || '', (r.matched_strategies || []).join('|')
        ]);

        const csv = toCsvString(headers, rows);
        downloadCsv(`AlphaVision_选股_${new Date().toISOString().slice(0, 10)}.csv`, csv);
    };

    const runAIReview = async () => {
        setAILoading(true);
        try {
            const candidates = filteredResults.slice(0, 10).map(stock => ({
                code: stock.代码, name: stock.名称, industry: stock.行业,
                strategy_type: stock.strategy_type, matched_strategies: stock.matched_strategies,
                data_date: stock.data_date || stock.date || stock.日期,
                as_of: stock.as_of, price: stock.现价, pct_chg: stock['涨幅%'],
                display_signal_score: stock.display_signal_score ?? stock.Score,
                display_quality_score: stock.display_quality_score ?? stock.sop_quality_score,
                display_opportunity_score: stock.display_opportunity_score ?? stock.trade_opportunity_score,
                trade_bucket: stock.trade_bucket, trade_eligible: stock.trade_eligible,
                trade_state: stock.trade_state, trade_blockers: stock.trade_blockers,
                trade_cautions: stock.trade_cautions,
                pa_trade_plan: stock.pa_trade_plan, pa_entry_price: stock.pa_entry_price,
                pa_stop_price: stock.pa_stop_price, pa_target_price: stock.pa_target_price,
                pa_risk_reward: stock.pa_risk_reward, pa_failure_risk: stock.pa_failure_risk,
                pa_volume_confirmed: stock.pa_volume_confirmed, pa_trend_damage: stock.pa_trend_damage,
                pa_decision_summary: stock.pa_decision_summary, price_action_risks: stock.price_action_risks,
                signal: stock.signal, tv_match: stock.tv_match, tv_ma_signal: stock.tv_ma_signal,
                tv_zp_signal: stock.tv_zp_signal, sector_phase: stock.sector_phase,
                sector_mainline: stock.sector_mainline, sector_role: stock.sector_role,
                sector_alignment_score: stock.sector_alignment_score, ROE: stock.ROE,
                净利YOY: stock.净利YOY, mkt_cap_yi: stock.mkt_cap_yi,
                money_flow: stock.money_flow, money_flow_status: stock.money_flow_status,
                历史胜率: stock.历史胜率, 信号次数: stock.信号次数, 回测统计: stock.回测统计,
                sop_risks: stock.sop_risks,
            }));
            const response = await api.post('/api/ai/analyze-candidates', { candidates });
            if (response.data?.status !== 'success') {
                setAISummary(response.data?.message || 'AI分析暂不可用');
                showToast(response.data?.message || 'AI分析暂不可用', 'error');
                return;
            }
            const reviews = (response.data.analyses || []) as AIReview[];
            setAIReviews(Object.fromEntries(reviews.map(item => [item.code, item])));
            setAISummary(response.data.market_summary || response.data.message || 'AI复核完成');
            try {
                const performance = await api.get('/api/ai/performance?days=365');
                setAIPerformance(performance.data as AIPerformance);
            } catch {
                setAIPerformance(null);
            }
            showToast(response.data.message || 'AI复核完成');
        } catch (error: unknown) {
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            const message = detail || 'AI分析请求失败，原策略结果不受影响';
            setAISummary(message);
            showToast(message, 'error');
        } finally {
            setAILoading(false);
        }
    };

    const addToWatchlist = async (stock: ScanResult, remark?: string, mode: 'SIMULATED' | 'REAL' = 'SIMULATED') => {
        try {
            const plan = stock.pa_trade_plan;
            const res = await api.post('/api/paper/add', {
                code: stock.代码,
                name: stock.名称,
                price: stock.现价,
                strategy_type: stock.strategy_type,
                remark: remark || undefined,
                trade_mode: mode,
                entry_source: 'scan_current_price',
                entry_signal_date: stock.date || stock.日期 || undefined,
                signal_sources: stock.signal_sources,
                entry_reason_snapshot: `${plan?.setup || stock.结构 || stock.price_action_pattern || '扫描入选'} / ${plan?.action_label || '未分级'} / Score ${stock.Score ?? '--'}`,
                pa_trade_action: plan?.action || stock.pa_trade_action,
                pa_trade_setup: plan?.setup || stock.pa_trade_setup,
                pa_entry_condition: plan?.entry_condition,
                pa_invalidation: plan?.invalidation,
                pa_risk_pct: plan?.risk_pct ?? stock.pa_risk_pct,
            });
            if (!['success', 'upgraded'].includes(res.data?.status)) {
                showToast(res.data?.detail || '加入失败，请在详情页补齐执行信息', 'error');
                return;
            }
            const modeLabel = mode === 'REAL' ? '实盘' : '模拟池';
            showToast(
                res.data?.status === 'upgraded'
                    ? `${stock.名称} 已升级现有持仓信号，未重复加仓`
                    : `${stock.名称} 已加入${modeLabel}`,
            );
        } catch (err) {
            console.error(err);
            showToast('加入失败，请重试', 'error');
        }
    };

    const addToObservation = async (stock: ScanResult) => {
        try {
            const plan = stock.pa_trade_plan;
            await api.post('/api/watchlist/add', {
                code: stock.代码,
                name: stock.名称,
                industry: stock.行业,
                watch_price: stock.现价,
                target_price: stock.pa_entry_price || stock.target_price,
                stop_price: stock.pa_stop_price || stock.stop_price,
                strategy_type: stock.strategy_type || 'squeeze',
                reason: plan?.entry_condition || `${stock.strategy_type || 'squeeze'} 扫描入选，Score ${stock.Score}`,
                invalidation: plan?.invalidation || (stock.stop_price ? `跌破 ${stock.stop_price}` : '跌破关键均线或策略失效'),
                source: 'scan',
                pa_trade_action: plan?.action || stock.pa_trade_action,
                pa_trade_setup: plan?.setup || stock.pa_trade_setup,
                pa_entry_condition: plan?.entry_condition,
                pa_invalidation: plan?.invalidation,
                pa_risk_pct: plan?.risk_pct ?? stock.pa_risk_pct,
            });
            showToast(`${stock.名称} 已加入观察池`);
        } catch (err) {
            console.error(err);
            showToast('加入观察池失败', 'error');
        }
    };

    const confirmAddToWatchlist = () => {
        if (remarkStock) {
            addToWatchlist(remarkStock, remarkText, addTradeMode);
            setRemarkStock(null);
            setRemarkText('');
            setAddTradeMode('SIMULATED');
        }
    };

    // ── Render a single stock row (shared between flat & grouped modes) ──
    const renderStockRow = (res: ScanResult, index: number) => {
        const aiReview = aiReviews[res.代码];
        const isBlocked = res.trade_bucket === 'BLOCK';
        const rowKey = getRowKey(res, index);
        const signalDisplayScore = clampScore(res.display_signal_score ?? res.Score);
        const qualityDisplayScore = clampScore(res.display_quality_score ?? res.sop_quality_score);
        const opportunityDisplayScore = clampScore(res.display_opportunity_score ?? res.trade_opportunity_score);
        const pctChange = typeof res["涨幅%"] === 'number' && Number.isFinite(res["涨幅%"])
            ? res["涨幅%"]
            : null;
        return (
        <React.Fragment key={rowKey}>
            <tr
                onClick={() => {
                    onSelectStock?.(res);
                    toggleRow(rowKey);
                }}
                className={cn(
                    "group transition-all cursor-pointer border-b border-slate-50",
                    expandedRow === rowKey ? "bg-indigo-50/50" : "hover:bg-indigo-50/20",
                    selectedCode === res.代码 && "bg-indigo-50/50 ring-1 ring-inset ring-indigo-100"
                )}
            >
                <td className="px-4 py-5">
                    <div className="flex items-center gap-3">
                        <div className="text-slate-400 transition-colors">
                            {expandedRow === rowKey ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        </div>
                        <div className="flex flex-col">
                            <div className="flex items-center gap-1.5">
                                <span className={cn("font-extrabold text-slate-700", isBlocked && "line-through")}>{res.名称}</span>
                                {res.warnings && res.warnings.length > 0 && (
                                    <div className="flex gap-1">
                                        {res.warnings.includes("📅 财报") && <Calendar size={10} className="text-amber-500 animate-pulse" />}
                                        {res.warnings.includes("🔒 解禁") && <Lock size={10} className="text-rose-500" />}
                                        {res.warnings.includes("⚠️ 减持") && <AlertTriangle size={10} className="text-rose-600" />}
                                    </div>
                                )}
                            </div>
                            <span className={cn("text-[10px] font-mono font-bold text-slate-400 tracking-tighter", isBlocked && "line-through")}>{res.代码}</span>
                            {(res.matched_strategies || []).length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1" aria-label="命中策略">
                                    {(res.matched_strategies || []).map(strategy => (
                                        <span key={strategy} className="rounded border border-indigo-100 bg-indigo-50 px-1.5 py-0.5 text-[8px] font-black text-indigo-700">
                                            命中 {strategy === 'h2' ? 'H2' : strategy}
                                        </span>
                                    ))}
                                </div>
                            )}
                            {(res.strategy_type === 'tv_dual' || res.strategy_type === 'tv_dual_strict' || res.strategy_type === 'sector_watch' || res.strategy_type === 'bottom_discovery') && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                    <span className="rounded border border-rose-100 bg-rose-50 px-1.5 py-0.5 text-[8px] font-black text-rose-600">
                                        均线 {res.tv_ma_signal || '--'}
                                    </span>
                                    <span className="rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-[8px] font-black text-emerald-700">
                                        ZP {res.tv_zp_signal || '--'}
                                    </span>
                                    <span className="rounded border border-sky-100 bg-sky-50 px-1.5 py-0.5 text-[8px] font-black text-sky-700">
                                        {res.tv_match || res.signal || 'TV命中'}
                                    </span>
                                    {res.early_watch_only && (
                                        <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[8px] font-black text-amber-700">
                                            早期观察
                                        </span>
                                    )}
                                    {res.momentum_watch_only && (
                                        <span className="rounded border border-orange-100 bg-orange-50 px-1.5 py-0.5 text-[8px] font-black text-orange-700">
                                            动量观察
                                        </span>
                                    )}
                                    {res.sector_watch_only && (
                                        <span className="rounded border border-indigo-100 bg-indigo-50 px-1.5 py-0.5 text-[8px] font-black text-indigo-700">
                                            板块观察
                                        </span>
                                    )}
                                    {res.bottom_discovery_watch_only && (
                                        <span className="rounded border border-cyan-100 bg-cyan-50 px-1.5 py-0.5 text-[8px] font-black text-cyan-700">
                                            {res.bottom_discovery_stage === 'B1_REVERSAL' ? '起涨预警' : '底部观察'}
                                        </span>
                                    )}
                                </div>
                            )}
                            {res.sequoia_research_shadow_only && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                    <span className="rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[8px] font-black text-violet-700">
                                        SHADOW研究 · 不可交易
                                    </span>
                                    {res.rps_120 != null && (
                                        <span className="rounded border border-sky-100 bg-sky-50 px-1.5 py-0.5 text-[8px] font-black text-sky-700">
                                            RPS120 {res.rps_120.toFixed(0)}
                                        </span>
                                    )}
                                    {res.trader_vic_2b_metrics && (
                                        <span className="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[8px] font-black text-amber-800">
                                            2B收复位 ¥{res.trader_vic_2b_metrics.vic_2b_support?.toFixed(2) ?? '--'} · MA200 ¥{res.trader_vic_2b_metrics.ma200?.toFixed(2) ?? '--'} · 20日斜率 {res.trader_vic_2b_metrics.ma200_slope_20d_pct?.toFixed(1) ?? '--'}% · 量比 {res.trader_vic_2b_metrics.vic_2b_volume_ratio?.toFixed(1) ?? '--'}x
                                        </span>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </td>

                {/* AI 二次复核列 */}
                <td className="px-3 py-5">
                    <div className="flex flex-col items-center gap-1">
                        <AIReviewBadge review={aiReview} />
                        {res.sop_quality_score != null && (
                            <span className="text-[8px] font-black text-slate-400">质量分 {qualityDisplayScore.toFixed(0)}/100</span>
                        )}
                        {res.sop_vetoes && res.sop_vetoes.length > 0 && (
                            <span className="text-[8px] text-rose-400 font-bold text-center leading-tight max-w-[60px]">
                                {res.sop_vetoes[0]}
                            </span>
                        )}
                    </div>
                </td>

                <td className="px-6 py-5">
                    <div className="flex flex-col items-center">
                        <div className="flex items-baseline gap-1">
                            <span className="text-lg font-black text-slate-800">{signalDisplayScore.toFixed(1)}</span>
                            <span className="text-[10px] font-bold text-indigo-500">信号/100</span>
                        </div>
                        <ConfidenceBadge score={signalDisplayScore} />
                    </div>
                </td>

                <td className="px-6 py-5">
                    <div className="flex flex-col items-center gap-2">
                        <div className={cn(
                            "text-sm font-bold px-2 py-0.5 rounded-lg",
                            pctChange == null ? "text-slate-400 bg-slate-50" :
                                pctChange >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"
                        )}>
                            {pctChange == null ? '--' : `${pctChange >= 0 ? '+' : ''}${pctChange.toFixed(2)}%`}
                        </div>
                        <div className="flex gap-3">
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] font-bold text-slate-300">RSI</span>
                                <span className="text-[10px] font-extrabold text-slate-500">{res.RSI}</span>
                            </div>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] font-bold text-slate-300">DIF</span>
                                <span className="text-[10px] font-extrabold text-slate-500">{res.DIF}</span>
                            </div>
                        </div>
                    </div>
                </td>

                <td className="px-4 py-5">
                    <div className="flex flex-col items-center gap-1.5 min-w-[120px]">
                        <div className="flex items-center gap-2">
                            <span className={cn(
                                "px-2 py-0.5 rounded-md text-[10px] font-black border",
                                (res.price_action_score || 0) >= 70 ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                    (res.price_action_score || 0) >= 55 ? "bg-blue-50 text-blue-700 border-blue-100" :
                                        "bg-slate-50 text-slate-500 border-slate-100"
                            )}>
                                PA {res.price_action_score ?? '--'}
                            </span>
                            {res.price_action_entry_quality && (
                                <span className="text-[9px] font-black text-slate-400">{res.price_action_entry_quality}</span>
                            )}
                        </div>
                        {(res.price_action_regime || res.结构) && (
                            <div className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded-md text-[10px] font-black border border-blue-100 max-w-[130px] truncate">
                                {res.price_action_regime || res.结构}
                            </div>
                        )}
                        {res.price_action_pattern && (
                            <span className="text-[10px] font-bold text-slate-600 max-w-[130px] truncate">{res.price_action_pattern}</span>
                        )}
                        {res.pa_monthly_trend && res.pa_monthly_state !== 'UNAVAILABLE' && (
                            <span className="text-[10px] font-bold text-indigo-600" title={`月线截至 ${res.pa_monthly_as_of || '未知'}；仅作研究标签`}>{res.pa_monthly_trend}</span>
                        )}
                        {res.pa_weekly_position && res.pa_weekly_position_state !== 'UNAVAILABLE' && (
                            <span className="text-[10px] font-bold text-amber-700" title={`周线截至 ${res.pa_weekly_position_as_of || '未知'}；仅作研究标签`}>{res.pa_weekly_position}</span>
                        )}
                        {res.pa_weekly_pattern_signals?.map((signal) => (
                            <span key={signal} className="text-[10px] font-bold text-amber-700" title={`周线截至 ${res.pa_weekly_position_as_of || '未知'}；观察标签，非买入信号`}>{signal}</span>
                        ))}
                        <div className="flex items-center gap-1">
                            <span className="text-[10px] font-bold text-slate-300">上影比</span>
                            <span className={cn(
                                "text-sm font-black",
                                (res.影线比 || 0) > 0.8 ? "text-rose-500" :
                                    (res.影线比 || 0) > 0.4 ? "text-amber-500" : "text-slate-600"
                            )}>
                                {(res.影线比 || 0).toFixed(2)}
                            </span>
                        </div>
                        {res.体质 && (
                            <div className="flex items-center gap-1 mt-0.5">
                                <span className="text-[9px] font-bold text-slate-300">沉积</span>
                                <span className="text-[10px] font-extrabold text-indigo-500">{res.体质}</span>
                            </div>
                        )}
                        {!res.体质 && <span className="text-[9px] text-slate-400 font-bold uppercase tracking-tighter">影线/实体</span>}
                    </div>
                </td>

                <td className="px-6 py-5">
                    <div className="flex flex-col items-center gap-2">
                        <div className="flex items-center gap-1.5 px-3 py-1 bg-indigo-50 text-indigo-600 border border-indigo-100 rounded-full min-w-[60px] justify-center">
                            <MapIcon size={10} className="text-indigo-400" />
                            <span className="text-[10px] font-black break-keep whitespace-nowrap">
                                {(res.行业 && res.行业.trim()) ? res.行业 : "未知"}
                            </span>
                        </div>
                        {res.共振 === "🔥 核心热点" && (
                            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-indigo-600 text-white rounded-lg shadow-lg shadow-indigo-100 animate-pulse">
                                <span className="text-[9px] font-black uppercase tracking-tighter">🔥 板块共振</span>
                            </div>
                        )}
                        <SectorTrendBadge trend={res.sector_trend} pct={res.sector_pct} />
                        <SectorMomentumBadge
                            score={res.sector_momentum_score}
                            breadth={res.sector_breadth}
                            phase={res.sector_phase}
                            rank={res.sector_rank}
                            alignment={res.sector_alignment_score}
                            role={res.sector_role}
                            pct3d={res.sector_3d_pct}
                            pct5d={res.sector_5d_pct}
                        />
                        {res.sector_mainline && (
                            <div className="text-[9px] font-black text-slate-600">
                                {res.sector_mainline} · 领导力 {res.leadership_score?.toFixed(0) ?? '--'}
                            </div>
                        )}
                        {res.trade_opportunity_score != null && (
                            <div className="text-[9px] font-black text-blue-700">
                                机会分 {opportunityDisplayScore.toFixed(0)}/100 · {res.trade_opportunity_label}
                            </div>
                        )}
                    </div>
                </td>

                <td className="px-6 py-5">
                    <div className="flex flex-col items-center gap-1">
                        {res.ROE !== undefined && res.ROE !== null ? (
                            <div className="flex flex-col items-center gap-1">
                                <div className="flex gap-2">
                                    <div className="flex flex-col items-center">
                                        <span className="text-[9px] font-bold text-slate-300">ROE</span>
                                        <span className={cn("text-[10px] font-extrabold", res.ROE >= 15 ? "text-rose-500" : res.ROE >= 8 ? "text-orange-500" : "text-slate-500")}>{res.ROE}%</span>
                                    </div>
                                    <div className="flex flex-col items-center">
                                        <span className="text-[9px] font-bold text-slate-300">净利YOY</span>
                                        <span className={cn("text-[10px] font-extrabold", (res.净利YOY ?? 0) >= 30 ? "text-rose-500" : (res.净利YOY ?? 0) >= 15 ? "text-orange-500" : "text-slate-500")}>{res.净利YOY}%</span>
                                    </div>
                                </div>
                                {((res.ROE ?? 0) >= 15 || (res.净利YOY ?? 0) >= 30) && (
                                    <span className="text-[9px] px-1.5 py-0.5 bg-rose-50 border border-rose-100 text-rose-500 rounded font-black mt-1">戴维斯双击💎</span>
                                )}
                            </div>
                        ) : (
                            <span className="text-[10px] text-slate-300 font-bold">---</span>
                        )}
                    </div>
                </td>

                <td className="px-6 py-5">
                    <div className="flex flex-col items-center gap-1">
                        <div className="flex items-center gap-1">
                            <span className="text-[10px] font-bold text-slate-300">资金</span>
                            <span className={cn(
                                "text-[10px] font-extrabold",
                                res.北向?.includes("流入") ? "text-rose-500" :
                                    res.北向?.includes("流出") ? "text-emerald-500" : "text-slate-400"
                            )}>
                                {res.北向 || "---"}
                            </span>
                        </div>
                    </div>
                </td>

                <td className="px-6 py-5">
                    <div className="flex flex-col items-center gap-1">
                        <div className="flex items-center gap-1 text-indigo-600">
                            <History size={14} strokeWidth={2.5} />
                            <span className="text-sm font-black italic">{res.历史胜率}</span>
                        </div>
                        <span className="text-[9px] font-bold text-slate-400 tracking-tighter">基于 {res.信号次数} 次历史共振信号</span>
                        {res.回测统计 && res.回测统计.avg_return !== 0 && (
                            <div className="flex flex-wrap justify-center gap-x-2 gap-y-0.5 mt-1">
                                <span className={`text-[9px] font-bold ${res.回测统计.avg_return >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                                    均收{res.回测统计.avg_return >= 0 ? '+' : ''}{res.回测统计.avg_return}%
                                </span>
                                <span className="text-[9px] font-bold text-emerald-500">
                                    回撤{res.回测统计.max_drawdown}%
                                </span>
                                <span className="text-[9px] font-bold text-amber-500">
                                    盈亏比{res.回测统计.profit_factor}
                                </span>
                                {res.回测统计.stop_loss_hits > 0 && (
                                    <span className="text-[9px] font-bold text-rose-400">
                                        止损{res.回测统计.stop_loss_hits}次
                                    </span>
                                )}
                            </div>
                        )}
                    </div>
                </td>

                <td className="px-8 py-5 text-right">
                    <div className="flex items-center justify-end gap-2">
                        <button
                            onClick={(e) => { e.stopPropagation(); setOptimizingStock(res); }}
                            className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all"
                            title="参数寻优"
                        >
                            <Settings2 size={18} />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); setSizingStock(res); }}
                            className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all"
                            title="仓位计算"
                        >
                            <CalcIcon size={18} />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); setRemarkStock(res); setRemarkText(''); }}
                            className="p-2 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-xl transition-all"
                            title="加入模拟池"
                        >
                            <Plus size={18} />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); addToObservation(res); }}
                            className="p-2 text-slate-400 hover:text-amber-600 hover:bg-amber-50 rounded-xl transition-all"
                            title="加入观察池"
                        >
                            <Eye size={18} />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); openChart(res.代码); }}
                            className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-slate-100 rounded-xl transition-all"
                            title="详情"
                        >
                            <ExternalLink size={18} />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); toggleRow(rowKey); }}
                            className={cn(
                                "p-2 rounded-xl transition-all",
                                expandedRow === rowKey ? "text-indigo-600 bg-indigo-50" : "text-slate-300 hover:text-rose-600 hover:bg-rose-50"
                            )}
                            title="查看K线"
                        >
                            <BarChart3 size={18} />
                        </button>
                    </div>
                </td>
            </tr>
            {expandedRow === rowKey && (
                <tr className="bg-slate-50/30 animate-in fade-in slide-in-from-top-2 duration-300">
                    <td colSpan={10} className="px-8 py-6">
                        <div className="flex flex-col gap-4">
                            {/* 系统操作建议卡片 */}
                            {res.entry_price && (
                                <div className="flex flex-wrap gap-3">
                                    <div className="flex-1 min-w-[200px] p-4 bg-gradient-to-br from-indigo-50 to-blue-50 rounded-2xl border border-indigo-100">
                                        <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-widest mb-2">📌 操作建议</div>
                                        <div className="grid grid-cols-2 gap-2 text-sm">
                                            <div><span className="text-slate-400 text-xs">入场价</span><div className="font-black text-indigo-600">¥{res.entry_price}</div></div>
                                            <div><span className="text-slate-400 text-xs">计划失效位</span><div className="font-black text-rose-500">¥{res.plan_stop_price || res.stop_price}</div></div>
                                            <div><span className="text-slate-400 text-xs">5日涨幅</span><div className={cn("font-bold", (res.pct_5d || 0) > 10 ? "text-rose-500" : "text-slate-600")}>{(res.pct_5d || 0) > 0 ? '+' : ''}{res.pct_5d?.toFixed(1)}%</div></div>
                                            <div><span className="text-slate-400 text-xs">流通市值</span><div className="font-bold text-slate-600">{res.mkt_cap_yi ? `${res.mkt_cap_yi}亿` : '---'}</div></div>
                                        </div>
                                    </div>
                                    {hasQualityDetail(res) && (
                                        <div className="flex-1 min-w-[200px] p-4 bg-slate-50 rounded-2xl border border-slate-100">
                                            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">质量与风控依据</div>
                                            <div className="flex flex-wrap gap-1.5">
                                                {res.sop_checks?.map((c, i) => <span key={i} className="text-[10px] px-2 py-0.5 bg-emerald-50 text-emerald-600 rounded-full font-bold border border-emerald-100">✅ {c}</span>)}
                                                {res.sop_bonuses?.map((b, i) => <span key={i} className="text-[10px] px-2 py-0.5 bg-amber-50 text-amber-600 rounded-full font-bold border border-amber-100">⭐ {b}</span>)}
                                                {res.sop_risks?.map((r, i) => <span key={i} className="text-[10px] px-2 py-0.5 bg-orange-50 text-orange-600 rounded-full font-bold border border-orange-100">⚠️ {r}</span>)}
                                                {res.sop_vetoes?.map((v, i) => <span key={i} className="text-[10px] px-2 py-0.5 bg-rose-50 text-rose-500 rounded-full font-bold border border-rose-100">❌ {v}</span>)}
                                            </div>
                                        </div>
                                    )}
                                    {aiReview && (
                                        <div className="flex-[2] min-w-[320px] p-4 bg-violet-50 rounded-2xl border border-violet-100">
                                            <div className="flex items-center justify-between gap-3">
                                                <div className="text-[10px] font-bold text-violet-500 uppercase tracking-widest">AI 二次复核</div>
                                                <AIReviewBadge review={aiReview} />
                                            </div>
                                            <p className="mt-2 text-sm font-bold text-slate-700 leading-relaxed">{aiReview.summary}</p>
                                            {!!aiReview.positive_factors?.length && <p className="mt-2 text-[11px] text-emerald-700">支持：{aiReview.positive_factors.join('；')}</p>}
                                            {!!aiReview.risk_factors?.length && <p className="mt-1 text-[11px] text-rose-600">风险：{aiReview.risk_factors.join('；')}</p>}
                                            {!!aiReview.data_limitations?.length && <p className="mt-1 text-[10px] text-slate-400">数据限制：{aiReview.data_limitations.join('；')}</p>}
                                        </div>
                                    )}
                                    {res.price_action_summary && (
                                        <div className="flex-[2] min-w-[360px] p-4 bg-blue-50 rounded-2xl border border-blue-100">
                                            <div className="flex items-start justify-between gap-3 mb-3">
                                                <div>
                                                    <div className="text-[10px] font-bold text-blue-500 uppercase tracking-widest">Al Brooks 价格行为解读</div>
                                                    <div className="text-sm font-black text-slate-800 mt-1">{res.price_action_summary}</div>
                                                </div>
                                                <span className={cn(
                                                    "text-[10px] font-black px-2 py-1 rounded-lg border shrink-0",
                                                    (res.price_action_score || 0) >= 72 ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                                        (res.price_action_score || 0) >= 55 ? "bg-white text-blue-700 border-blue-100" :
                                                            "bg-amber-50 text-amber-700 border-amber-100"
                                                )}>
                                                    PA {res.price_action_score ?? '--'} · {explainBrooksScore(res.price_action_score)}
                                                </span>
                                            </div>
                                            {(res.pa_decision_summary || res.pa_multi_timeframe_note) && (
                                                <div className="mt-3 p-3 bg-white/80 border border-blue-100 rounded-xl">
                                                    <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">综合判断</div>
                                                    <p className="text-xs font-bold text-slate-700 leading-relaxed">{res.pa_decision_summary || res.pa_multi_timeframe_note}</p>
                                                </div>
                                            )}
                                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mt-3 text-xs">
                                                <BrooksInfo label="触发价" value={res.pa_entry_price ? `¥${res.pa_entry_price}` : '--'} note="有效突破后才算入场" tone="blue" />
                                                <BrooksInfo
                                                    label="双层失效"
                                                    value={res.pa_close_guard_price ? `收盘 ¥${res.pa_close_guard_price}` : (res.pa_stop_price ? `¥${res.pa_stop_price}` : '--')}
                                                    note={res.pa_hard_stop_price ? `盘中硬止损 ¥${res.pa_hard_stop_price}` : '跌破则结构失效'}
                                                    tone="rose"
                                                />
                                                <BrooksInfo label="目标价" value={res.pa_target_price ? `¥${res.pa_target_price}` : '--'} note="按结构风险测算" tone="emerald" />
                                                <BrooksInfo label="策略倾向" value={res.pa_position_strategy || '--'} note={res.final_rank_score != null ? `排序分 ${res.final_rank_score} / Brooks ${res.brooks_rank_adjustment || 0}` : (res.pa_risk_reward ? `${res.pa_risk_reward}R · ${explainRiskReward(res.pa_risk_reward)}` : '等待结构确认')} />
                                            </div>
                                            <details className="mt-3 group">
                                                <summary className="cursor-pointer select-none text-[10px] font-black text-blue-600 uppercase tracking-widest hover:text-blue-700">展开 Brooks 细节</summary>
                                                <div className="mt-3 space-y-3">
                                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                                                        <BrooksInfo label="市场结构" value={res.pa_market_cycle || res.price_action_regime || '--'} note="判断趋势/区间环境" />
                                                        <BrooksInfo label="信号K" value={res.price_action_signal || '--'} note="最后一根K的多空主动性" />
                                                        <BrooksInfo label="形态" value={res.price_action_pattern || res.结构 || '--'} note="当前可交易结构" />
                                                        <BrooksInfo label="区间位置" value={res.pa_range_location || '--'} note="追价或低吸的位置判断" />
                                                    </div>
                                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                                                        <BrooksInfo label="回调结构" value={res.pa_pullback_structure || '--'} note={res.pa_pullback_legs != null ? `${res.pa_pullback_legs} 腿回调` : '等待结构确认'} />
                                                        <BrooksInfo label="回踩有效性" value={res.pa_pullback_status_label || '--'} note={res.pa_pullback_confirmation_price ? `确认 >${res.pa_pullback_confirmation_price} · 失效 <${res.pa_pullback_invalidation_price || '--'}` : '等待价量确认'} tone={res.pa_pullback_status === 'CONFIRMED' ? "emerald" : res.pa_pullback_status === 'INVALIDATED' ? "rose" : "slate"} />
                                                        <BrooksInfo label="失败风险" value={explainFailureRisk(res.pa_failure_risk)} note="假突破/上影/位置风险" tone={(res.pa_failure_risk || 0) >= 70 ? "rose" : undefined} />
                                                        <BrooksInfo label="陷阱风险" value={explainFailureRisk(res.pa_trap_risk)} note={res.pa_failed_breakout_type || '多头陷阱风险'} tone={(res.pa_trap_risk || 0) >= 70 ? "rose" : undefined} />
                                                    </div>
                                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                                                        <BrooksInfo label="H2质量" value={explainH2Quality(res.pa_h2_quality, res.pa_entry_quality_score)} note="回踩位置与信号K质量" tone={(res.pa_entry_quality_score || 0) >= 75 ? "emerald" : "slate"} />
                                                        <BrooksInfo label="区间规则" value={res.pa_range_rule || '--'} note="Brooks区间交易原则" />
                                                        <BrooksInfo label="假突破" value={res.pa_failed_breakout_type || '--'} note="突破后是否失去延续" tone={res.pa_failed_breakout_type ? "rose" : "slate"} />
                                                        <BrooksInfo label="区间位置" value={res.pa_range_location || '--'} note="上沿谨慎，下沿看反转" />
                                                    </div>
                                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                                                        <BrooksInfo label="微型通道" value={res.pa_micro_channel || '--'} note="连续高低点方向" tone={res.pa_micro_channel === '多头微型通道' ? "emerald" : "slate"} />
                                                        <BrooksInfo label="Always In" value={res.pa_always_in_strength != null ? `${res.pa_always_in_strength}分` : '--'} note="趋势持续强度" tone={(res.pa_always_in_strength || 0) >= 70 ? "emerald" : "slate"} />
                                                        <BrooksInfo label="趋势破坏" value={res.pa_trend_damage || '--'} note="EMA/短线结构破坏" tone={res.pa_trend_damage && res.pa_trend_damage !== '无' ? "rose" : "slate"} />
                                                        <BrooksInfo label="通道状态" value={res.pa_channel_state || '--'} note="延续、过冲或假破" />
                                                    </div>
                                                    <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 text-xs">
                                                        <BrooksInfo label="多周期" value={res.pa_weekly_context || '--'} note={res.pa_multi_timeframe_score != null ? `${res.pa_multi_timeframe_score > 0 ? '+' : ''}${res.pa_multi_timeframe_score}分` : '周线确认'} tone={(res.pa_multi_timeframe_score || 0) < 0 ? "rose" : "slate"} />
                                                        <BrooksInfo label="缩量回踩" value={res.pa_volume_pullback_label || '--'} note={res.pa_volume_pullback_support_price ? `${res.pa_volume_pullback_confirmation_label || '等待右侧确认'} · 支撑 ¥${res.pa_volume_pullback_support_price} · 执行分 ${res.pa_volume_pullback_score_delta && res.pa_volume_pullback_score_delta > 0 ? '+' : ''}${res.pa_volume_pullback_score_delta || 0}` : '需先出现真实放量突破'} tone={res.pa_volume_pullback_status === 'CONFIRMED' ? "emerald" : res.pa_volume_pullback_status === 'INVALIDATED' ? "rose" : "slate"} />
                                                        <BrooksInfo label="量能行为" value={res.pa_volume_pattern || '--'} note={res.pa_volume_confirmed ? '量能确认' : (res.pa_volume_risk || '等待确认')} tone={res.pa_volume_confirmed ? "emerald" : "slate"} />
                                                        <BrooksInfo label="缺口行为" value={res.pa_gap_type || '--'} note={res.pa_gap_risk != null ? `风险 ${res.pa_gap_risk}%` : '无明显缺口'} tone={(res.pa_gap_risk || 0) >= 70 ? "rose" : "slate"} />
                                                        <BrooksInfo label="趋势阶段" value={res.pa_trend_phase || '--'} note={res.pa_trend_phase_action || '等待结构确认'} />
                                                    </div>
                                                    <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 text-xs">
                                                        <BrooksInfo label="月线方向·研究" value={res.pa_monthly_trend || '--'} note={res.pa_monthly_as_of ? `已完成月线截至 ${res.pa_monthly_as_of}` : '历史数据不足'} tone={res.pa_monthly_state === 'DOWN' ? "rose" : "slate"} />
                                                        <BrooksInfo label="周线位置·研究" value={res.pa_weekly_position || '--'} note={res.pa_weekly_position_as_of ? `已完成周线截至 ${res.pa_weekly_position_as_of}` : '历史数据不足'} tone={res.pa_weekly_position_state === 'EXTENDED' || res.pa_weekly_position_state === 'WEAK' ? "rose" : "slate"} />
                                                        <BrooksInfo label="日线买点路径·研究" value={res.pa_swing_entry_route === 'BREAKOUT' ? '放量突破' : res.pa_swing_entry_route === 'PULLBACK' ? '回调转强' : '等待新触发'} note="不改变当前交易分类与退出规则" />
                                                    </div>
                                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                                                        <BrooksInfo label="失败二次入场" value={res.pa_failed_second_entry || '--'} note={res.pa_second_entry_risk ? `风险 ${res.pa_second_entry_risk}%` : '未触发'} tone={res.pa_failed_second_entry === '失败H2' ? "rose" : "slate"} />
                                                        <BrooksInfo label="区间宽度" value={res.pa_range_width_quality || '--'} note={res.pa_range_center_risk != null ? `中轴风险 ${res.pa_range_center_risk}%` : '等待判断'} />
                                                        <BrooksInfo label="区间假突破数" value={res.pa_range_failed_breakout_count ?? '--'} note="近20根上沿失败次数" tone={(res.pa_range_failed_breakout_count || 0) >= 2 ? "rose" : "slate"} />
                                                        <BrooksInfo label="周线说明" value={res.pa_multi_timeframe_note || '--'} note="多周期确认细节" />
                                                    </div>
                                                </div>
                                            </details>
                                            <div className="mt-3 p-3 bg-white/80 border border-blue-100 rounded-xl">
                                                <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">建议操作</div>
                                                <p className="text-xs font-bold text-slate-700 leading-relaxed">{explainBrooksAction(res)}</p>
                                                {res.pa_trade_plan?.entry_condition && (
                                                    <p className="text-[11px] font-semibold text-slate-500 mt-1">触发条件：{res.pa_trade_plan.entry_condition}</p>
                                                )}
                                            </div>
                                            {res.price_action_risks && res.price_action_risks.length > 0 && (
                                                <div className="mt-3 flex flex-wrap gap-1.5">
                                                    {res.price_action_risks.map((risk, i) => (
                                                        <span key={i} className="text-[10px] px-2 py-0.5 bg-white text-slate-500 rounded-full font-bold border border-blue-100">{risk}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                    {res.pa_trade_plan && (
                                        <div className="flex-1 min-w-[300px] p-4 bg-white rounded-2xl border border-slate-200 shadow-sm">
                                            <div className="flex items-center justify-between gap-3 mb-3">
                                                <div>
                                                    <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Brooks 交易计划</div>
                                                    <div className="text-sm font-black text-slate-800">{res.pa_trade_plan.setup}</div>
                                                </div>
                                                <span className={cn(
                                                    "text-[10px] font-black px-2 py-1 rounded-lg border",
                                                    res.pa_trade_plan.action === 'READY' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                                                        res.pa_trade_plan.action === 'AVOID' ? "bg-rose-50 text-rose-700 border-rose-100" :
                                                            "bg-amber-50 text-amber-700 border-amber-100"
                                                )}>
                                                    {res.pa_trade_plan.action_label}
                                                </span>
                                            </div>
                                            <div className="space-y-2 text-xs font-semibold text-slate-600">
                                                <p><span className="text-slate-400">触发：</span>{res.pa_trade_plan.entry_condition}</p>
                                                <p><span className="text-slate-400">失效：</span>{res.pa_trade_plan.invalidation}</p>
                                                <p><span className="text-slate-400">仓位：</span>{res.pa_trade_plan.position_hint} / 风险 {res.pa_trade_plan.risk_pct || 0}%</p>
                                                <p><span className="text-slate-400">有效确认：</span>{res.execution_plan_state?.active_confirmation_price || res.pa_entry_price || '--'} / {res.execution_plan_state?.state || 'GENERATED_PLAN'}</p>
                                                <p><span className="text-slate-400">执行盈亏比：</span>{res.execution_rr?.execution_rr ?? res.pa_risk_reward ?? '--'}</p>
                                            </div>
                                            {!!res.distance_to_trade?.steps?.length && (
                                                <div className="mt-3 rounded-xl bg-amber-50 border border-amber-100 p-2 text-[11px] font-bold text-amber-700">
                                                    距可交易：{res.distance_to_trade.steps.join('；')}
                                                </div>
                                            )}
                                            {res.pa_trade_plan.avoid_reasons?.length > 0 && (
                                                <div className="mt-3 flex flex-wrap gap-1.5">
                                                    {res.pa_trade_plan.avoid_reasons.map((reason, i) => (
                                                        <span key={i} className="text-[10px] px-2 py-0.5 bg-slate-50 text-slate-500 rounded-full font-bold border border-slate-100">{reason}</span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="px-3 py-1 bg-white border border-slate-200 rounded-lg text-xs font-bold text-slate-600 shadow-sm">
                                        📈 动态 K 线集成
                                    </div>
                                    <span className="text-xs text-slate-400 font-bold font-mono tracking-widest">{res.名称} {res.代码}</span>
                                </div>
                                <div className="flex gap-4">
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            openTimeline(res.代码);
                                        }}
                                        className="flex items-center gap-1.5 text-xs font-bold text-amber-600 hover:text-amber-700 transition-colors"
                                    >
                                        <History size={12} />计划时间线
                                    </button>
                                    <button
                                        onClick={(e) => { e.stopPropagation(); openChart(res.代码); }}
                                        className="flex items-center gap-1.5 text-xs font-bold text-indigo-600 hover:text-indigo-700 transition-colors"
                                    >
                                        <ExternalLink size={12} />
                                        东财详情
                                    </button>
                                    <button
                                        onClick={(e) => { e.stopPropagation(); toggleRow(rowKey); }}
                                        className="text-xs font-bold text-slate-400 hover:text-slate-600 transition-colors"
                                    >
                                        收起图表
                                    </button>
                                </div>
                            </div>
                            <div className="w-full h-[450px] bg-white rounded-2xl border border-slate-100 shadow-xl overflow-hidden relative group/chart">
                                <StockChart code={res.代码} name={res.名称} strategyType={res.strategy_type} />
                                <div className="absolute inset-x-0 bottom-0 py-2 px-4 bg-white/90 backdrop-blur-sm border-t border-slate-50 flex justify-between items-center opacity-0 group-hover/chart:opacity-100 transition-opacity">
                                    <span className="text-[10px] font-bold text-slate-400">数据源: 本地数据库 (极速渲染)</span>
                                    <span className="text-[10px] font-bold text-indigo-400 italic">Alpha Vision 共振信号确认区</span>
                                </div>
                            </div>
                        </div>
                    </td>
                </tr>
            )}
        </React.Fragment>
    );
    };

    // ── Sector group header row ──
    const renderSectorHeader = (sector: string, stocks: ScanResult[]) => {
        const avgScore = (stocks.reduce((s, r) => s + clampScore(r.display_signal_score ?? r.Score), 0) / stocks.length).toFixed(1);
        const roeStocks = stocks.filter(r => r.ROE !== undefined && r.ROE !== null && r.ROE > 0);
        const avgROE = roeStocks.length > 0 ? (roeStocks.reduce((s, r) => s + (r.ROE || 0), 0) / roeStocks.length).toFixed(1) : null;
        const isCollapsed = collapsedSectors.has(sector);
        const hasResonance = stocks.some(r => r.共振 === "🔥 核心热点");

        return (
            <tr
                key={`sector-${sector}`}
                onClick={() => toggleSectorCollapse(sector)}
                className={cn(
                    "cursor-pointer transition-all border-b-2 border-indigo-100",
                    hasResonance
                        ? "bg-gradient-to-r from-indigo-50 via-purple-50/50 to-indigo-50 hover:from-indigo-100 hover:via-purple-100/50 hover:to-indigo-100"
                        : "bg-slate-50/80 hover:bg-slate-100/80"
                )}
            >
                <td colSpan={10} className="px-8 py-4">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className={cn(
                                "transition-transform duration-200",
                                isCollapsed ? "-rotate-90" : "rotate-0"
                            )}>
                                <ChevronDown size={16} className="text-indigo-400" />
                            </div>
                            <div className="flex items-center gap-2">
                                <Layers size={16} className={hasResonance ? "text-indigo-600" : "text-slate-400"} />
                                <span className="text-sm font-black text-slate-700">{sector}</span>
                            </div>
                            <div className="flex items-center gap-2 ml-2">
                                <span className={cn(
                                    "px-2.5 py-0.5 rounded-full text-[10px] font-black",
                                    stocks.length >= 3
                                        ? "bg-indigo-600 text-white shadow-sm shadow-indigo-200"
                                        : "bg-slate-200 text-slate-600"
                                )}>
                                    {stocks.length} 只
                                </span>
                                {stocks.length >= 3 && (
                                    <span className="text-[9px] font-bold text-indigo-500 bg-indigo-50 px-2 py-0.5 rounded-full border border-indigo-100 animate-pulse">
                                        🔥 板块聚集
                                    </span>
                                )}
                                {hasResonance && (
                                    <span className="text-[9px] font-bold text-purple-600 bg-purple-50 px-2 py-0.5 rounded-full border border-purple-100">
                                        ⚡ 共振热点
                                    </span>
                                )}
                            </div>
                        </div>
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] font-bold text-slate-400">信号均分/100</span>
                                <span className="text-sm font-black text-indigo-600">{avgScore}</span>
                            </div>
                            {avgROE && (
                                <div className="flex items-center gap-1">
                                    <span className="text-[10px] font-bold text-slate-400">均ROE</span>
                                    <span className={cn("text-sm font-black", Number(avgROE) >= 15 ? "text-rose-500" : "text-slate-600")}>{avgROE}%</span>
                                </div>
                            )}
                        </div>
                    </div>
                </td>
            </tr>
        );
    };

    return (
        <div className="glass-card overflow-hidden border-none shadow-2xl shadow-slate-200/50 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {(timeline || timelineLoading) && (
                <ModalOverlay
                    open
                    onClose={() => { if (!timelineLoading) setTimeline(null); }}
                    labelledBy="results-timeline-dialog-title"
                    zIndexClass="fixed inset-0 z-50 bg-slate-950/40 backdrop-blur-sm flex items-center justify-center p-4"
                >
                    <div className="w-full max-w-3xl max-h-[80vh] overflow-auto rounded-2xl bg-white p-5 shadow-2xl">
                        <div className="flex items-center justify-between mb-4">
                            <div><h3 id="results-timeline-dialog-title" className="font-black text-slate-800">执行计划时间线</h3><p className="text-xs text-slate-400">{timeline?.code || '加载中'}</p></div>
                            <button onClick={() => setTimeline(null)} className="text-xs font-black text-slate-500">关闭</button>
                        </div>
                        {timelineLoading ? <div className="p-10 text-center text-slate-400">加载中...</div> : (
                            <div className="space-y-3">
                                {(timeline?.items || []).map((item, index) => (
                                    <div key={`${item.scanned_at}-${index}`} className="rounded-xl border border-slate-100 bg-slate-50 p-3">
                                        <div className="flex justify-between text-xs font-black text-slate-700"><span>{item.data_date} · {item.strategy_type}</span><span>{item.trade_bucket || '--'}</span></div>
                                        <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] text-slate-500">
                                            <span>现价 {item.price ?? '--'}</span><span>确认 {item.confirmation_price ?? '--'}</span><span>止损 {item.stop_price ?? '--'}</span><span>目标 {item.target_price ?? '--'}</span>
                                        </div>
                                        <div className="mt-2 text-[11px] font-bold text-amber-700">状态：{item.plan_state?.state || item.lifecycle || '--'}</div>
                                        {!!item.blockers?.length && <div className="mt-1 text-[10px] text-slate-400">{item.blockers.slice(0, 3).join('；')}</div>}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </ModalOverlay>
            )}
            <div className="px-8 py-6 border-b border-slate-100 flex items-center justify-between bg-white">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-indigo-50 text-indigo-600 rounded-xl flex items-center justify-center">
                        <Target size={20} />
                    </div>
                    <div>
                        <h3 className="text-lg font-bold text-slate-800">多因子共振池</h3>
                        <p className="text-xs text-slate-400 font-bold uppercase tracking-widest">Resonance Selection (Top {results.length})</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {/* Sector group toggle */}
                    <button
                        onClick={() => { setGroupBySector(!groupBySector); setCollapsedSectors(new Set()); }}
                        className={cn(
                            "flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold transition-all border-2",
                            groupBySector
                                ? "bg-indigo-50 border-indigo-200 text-indigo-600 shadow-sm shadow-indigo-100"
                                : "bg-slate-50 border-transparent text-slate-500 hover:bg-slate-100"
                        )}
                    >
                        <Layers size={16} />
                        板块分组
                    </button>
                    {/* Sort dropdown (only visible when grouped) */}
                    {groupBySector && (
                        <button
                            onClick={() => setSectorSortKey(sectorSortKey === 'count' ? 'avgScore' : 'count')}
                            className="flex items-center gap-1.5 px-3 py-2 bg-slate-50 text-slate-500 rounded-xl text-xs font-bold hover:bg-slate-100 transition-all"
                            title="切换板块排序方式"
                        >
                            <ArrowUpDown size={14} />
                            {sectorSortKey === 'count' ? '按数量排序' : '按均分排序'}
                        </button>
                    )}
                    <button
                        onClick={runAIReview}
                        disabled={aiLoading || filteredResults.length === 0}
                        className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-bold transition-all border-2 bg-violet-50 border-violet-200 text-violet-700 hover:bg-violet-100 disabled:opacity-50"
                    >
                        {aiLoading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                        {aiLoading ? 'AI分析中' : 'AI二次分析'}
                    </button>
                    <select
                        value={brooksFilter}
                        onChange={e => setBrooksFilter(e.target.value as typeof brooksFilter)}
                        className="px-3 py-2 bg-slate-50 text-slate-600 rounded-xl text-xs font-black border-2 border-transparent outline-none hover:bg-slate-100"
                        title="Brooks价格行为过滤"
                    >
                        <option value="ALL">Brooks 全部</option>
                        <option value="READY">仅 READY</option>
                        <option value="NO_AVOID">排除 AVOID</option>
                        <option value="LOW_RISK">风险≤8%</option>
                        <option value="LOW_FAILURE">低失败风险</option>
                        <option value="H2_STRONG">强H2质量</option>
                        <option value="STRONG_TREND">强趋势</option>
                        <option value="PULLBACK">H2/回踩</option>
                    </select>
                    <button
                        onClick={handleExport}
                        className="flex items-center gap-2 px-4 py-2 bg-slate-50 text-slate-500 rounded-xl text-sm font-bold hover:bg-slate-100 transition-all"
                    >
                        <ExternalLink size={16} />
                        导出选股单
                    </button>
                </div>
            </div>

            {aiSummary && (
                <div className="mx-8 mt-4 rounded-xl border border-violet-100 bg-violet-50 px-4 py-3 text-xs font-bold text-violet-800">
                    <span className="mr-2">AI复核摘要：</span>{aiSummary}
                    {aiPerformance?.status === 'available'
                        && (aiFiveDayPerformance?.buy?.signals ?? 0) > 0 && (
                        <div className="mt-2 border-t border-violet-100 pt-2 text-[11px] text-violet-600">
                            历史5日验证：AI BUY {aiFiveDayPerformance?.buy?.signals} 笔，
                            胜率 {aiFiveDayPerformance?.buy?.win_rate}% / 原候选池 {aiFiveDayPerformance?.all_reviewed?.win_rate ?? 0}%
                            {aiFiveDayPerformance?.buy_lift?.win_rate_pct_points != null
                                ? `，提升 ${aiFiveDayPerformance.buy_lift.win_rate_pct_points} 个百分点`
                                : ''}
                            {aiPerformance.sample_warning ? `。${aiPerformance.sample_warning}` : ''}
                        </div>
                    )}
                </div>
            )}
            <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                    <thead>
                        <tr className="bg-slate-50/50">
                            <th className="px-4 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest">股票信息</th>
                            <th className="px-3 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">AI复核</th>
                            <th className="px-4 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">
                                <div className="flex items-center justify-center gap-1 group/tooltip cursor-help relative">
                                    综合强度
                                    <HelpCircle size={10} />
                                    <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 w-48 p-2 bg-slate-800 text-white text-[9px] rounded-lg opacity-0 group-hover/tooltip:opacity-100 transition-opacity pointer-events-none z-50 normal-case font-medium leading-relaxed">
                                        计算公式：<br />
                                        (量比 × 20) + (粘合度贡献 × 4000) + (RSI × 0.5)<br />
                                        分数越高代表量价配合越完美。
                                    </div>
                                </div>
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">
                                <div className="flex items-center justify-center gap-1 group/tooltip cursor-help relative">
                                    技术指标
                                    <HelpCircle size={10} />
                                    <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 w-48 p-2 bg-slate-800 text-white text-[9px] rounded-lg opacity-0 group-hover/tooltip:opacity-100 transition-opacity pointer-events-none z-50 normal-case font-medium leading-relaxed">
                                        包含：<br />
                                        涨幅：当日价格变动<br />
                                        RSI：14日相对强弱指标<br />
                                        DIF：MACD 核心差值<br />
                                        板块共振：同行业多股同发
                                    </div>
                                </div>
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">价格形态</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">所属板块</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">基本面(最新季)</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">资金动向</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">历史表现</th>
                            <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-right">操作</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                        {sectorGroups ? (
                            /* ── Grouped mode ── */
                            sectorGroups.map(([sector, stocks]) => {
                                const displayStocks = stocks;
                                return (
                                <React.Fragment key={`group-${sector}`}>
                                    {renderSectorHeader(sector, displayStocks)}
                                    {!collapsedSectors.has(sector) && displayStocks.map(renderStockRow)}
                                </React.Fragment>
                            );
                            })
                        ) : (
                            /* ── Flat mode (original) ── */
                            filteredResults.map(renderStockRow)
                        )}
                    </tbody>
                </table>
            </div>

            {sizingStock && (
                <PositionSizer
                    stock={sizingStock}
                    onClose={() => setSizingStock(null)}
                />
            )}

            {optimizingStock && (
                <HeatmapOptimizer
                    code={optimizingStock.代码}
                    name={optimizingStock.名称}
                    strategy={optimizingStock.strategy_type || 'squeeze'}
                    onClose={() => setOptimizingStock(null)}
                />
            )}

            {remarkStock && (
                <ModalOverlay
                    open
                    onClose={() => { setRemarkStock(null); setRemarkText(''); setAddTradeMode('SIMULATED'); }}
                    labelledBy="results-remark-dialog-title"
                    zIndexClass="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
                >
                    <div className="bg-white rounded-2xl shadow-2xl p-6 w-[400px] space-y-4 animate-in fade-in zoom-in-95 duration-200">
                        <div className="flex items-center justify-between">
                            <h3 id="results-remark-dialog-title" className="text-sm font-bold text-slate-800">加入交易记录</h3>
                            <span className="text-xs text-slate-400 font-mono">{remarkStock.名称} {remarkStock.代码}</span>
                        </div>
                        {/* Trade Mode Selector */}
                        <div>
                            <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest block mb-1.5">交易模式</label>
                            <div className="flex bg-slate-100 rounded-xl p-1 gap-1">
                                <button
                                    onClick={() => setAddTradeMode('SIMULATED')}
                                    className={cn(
                                        "flex-1 py-2 text-xs font-black rounded-lg transition-all",
                                        addTradeMode === 'SIMULATED'
                                            ? "bg-gradient-to-r from-blue-500 to-indigo-500 text-white shadow-md"
                                            : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    🔵 模拟盘
                                </button>
                                <button
                                    onClick={() => setAddTradeMode('REAL')}
                                    className={cn(
                                        "flex-1 py-2 text-xs font-black rounded-lg transition-all",
                                        addTradeMode === 'REAL'
                                            ? "bg-gradient-to-r from-rose-500 to-red-500 text-white shadow-md"
                                            : "text-slate-400 hover:text-slate-600"
                                    )}
                                >
                                    🔴 实盘
                                </button>
                            </div>
                            {addTradeMode === 'REAL' && (
                                <p className="text-[10px] text-rose-500 font-bold mt-1.5">⚠️ 实盘记录将标记为真实交易</p>
                            )}
                        </div>
                        <div>
                            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest block mb-1.5">加入原因 / 备注</label>
                            <textarea
                                value={remarkText}
                                onChange={e => setRemarkText(e.target.value)}
                                placeholder="例如：均线粘合突破，放量突破前高..."
                                rows={3}
                                className="w-full px-3 py-2 text-sm text-slate-800 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
                                autoFocus
                                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); confirmAddToWatchlist(); } }}
                            />
                        </div>
                        <div className="flex justify-end gap-2">
                            <button
                                onClick={() => { setRemarkStock(null); setRemarkText(''); setAddTradeMode('SIMULATED'); }}
                                className="px-4 py-2 text-xs font-bold text-slate-400 hover:text-slate-600 rounded-xl transition-all"
                            >
                                取消
                            </button>
                            <button
                                onClick={confirmAddToWatchlist}
                                className={cn(
                                    "px-4 py-2 text-xs font-bold text-white rounded-xl transition-all",
                                    addTradeMode === 'REAL'
                                        ? "bg-rose-600 hover:bg-rose-700"
                                        : "bg-indigo-600 hover:bg-indigo-700"
                                )}
                            >
                                {addTradeMode === 'REAL' ? '确认加入实盘' : '确认加入模拟'}
                            </button>
                        </div>
                    </div>
                </ModalOverlay>
            )}

            {/* Toast notification */}
            {toast && (
                <div className={cn(
                    "fixed top-6 right-6 z-50 px-5 py-3 rounded-2xl shadow-2xl text-sm font-bold animate-in fade-in slide-in-from-top-2 duration-300",
                    toast.type === 'success' ? "bg-emerald-600 text-white" : "bg-rose-600 text-white"
                )}>
                    {toast.message}
                </div>
            )}
        </div>
    );
}

function ConfidenceBadge({ score }: { score: number }) {
    if (score > 100) return <span className="text-[9px] font-bold text-purple-500 bg-purple-50 px-2 rounded-md mt-1">💎 极高信心</span>;
    if (score > 80) return <span className="text-[9px] font-bold text-rose-500 bg-rose-50 px-2 rounded-md mt-1">🔥 强突破势</span>;
    if (score > 60) return <span className="text-[9px] font-bold text-emerald-500 bg-emerald-50 px-2 rounded-md mt-1">📊 稳定共振</span>;
    return <span className="text-[9px] font-bold text-slate-400 bg-slate-100 px-2 rounded-md mt-1">🔍 持续观察</span>;
}

function hasQualityDetail(res: ScanResult): boolean {
    return Boolean(res.sop_vetoes?.length || res.sop_risks?.length || res.sop_checks?.length || res.sop_bonuses?.length);
}

function AIReviewBadge({ review }: { review?: AIReview }) {
    if (!review) return <span className="text-[9px] font-bold text-slate-400">待分析</span>;
    const style = review.action === 'BUY'
        ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
        : review.action === 'AVOID'
            ? 'bg-rose-100 text-rose-700 border-rose-200'
            : 'bg-amber-100 text-amber-700 border-amber-200';
    const label = review.action === 'BUY' ? '复核买入' : review.action === 'AVOID' ? '回避' : '等待';
    return (
        <span title={review.summary} className={cn('rounded-lg border px-2 py-1 text-[10px] font-black', style)}>
            {label} {review.confidence}%
        </span>
    );
}

function BrooksInfo({
    label,
    value,
    note,
    tone = 'slate'
}: {
    label: string;
    value: string | number;
    note: string;
    tone?: 'slate' | 'blue' | 'rose' | 'emerald';
}) {
    const toneClass = {
        slate: 'text-slate-800',
        blue: 'text-blue-700',
        rose: 'text-rose-600',
        emerald: 'text-emerald-600',
    }[tone];

    return (
        <div className="p-2.5 bg-white/80 border border-blue-100 rounded-xl">
            <div className="text-[10px] font-bold text-slate-400">{label}</div>
            <div className={cn("text-xs font-black mt-0.5 truncate", toneClass)}>{value}</div>
            <div className="text-[10px] font-semibold text-slate-400 mt-1 leading-snug">{note}</div>
        </div>
    );
}

function SectorTrendBadge({ trend, pct }: { trend?: string; pct?: number }) {
    if (!trend || trend === 'UNKNOWN') return null;
    const config: Record<string, { icon: string; color: string; bg: string; border: string }> = {
        'LEAD':   { icon: '🚀', color: 'text-rose-600',    bg: 'bg-rose-50',    border: 'border-rose-100' },
        'FOLLOW': { icon: '📈', color: 'text-orange-500',  bg: 'bg-orange-50',  border: 'border-orange-100' },
        'FLAT':   { icon: '➖', color: 'text-slate-500',   bg: 'bg-slate-50',   border: 'border-slate-100' },
        'DOWN':   { icon: '📉', color: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-100' },
    };
    const c = config[trend] || config['FLAT'];
    const pctStr = pct !== undefined ? `${pct >= 0 ? '+' : ''}${pct}%` : '';
    return (
        <div className={cn("flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-bold border mt-1", c.color, c.bg, c.border)}>
            {c.icon} {pctStr}
        </div>
    );
}

function SectorMomentumBadge({
    score,
    breadth,
    phase,
    rank,
    alignment,
    role,
    pct3d,
    pct5d
}: {
    score?: number;
    breadth?: number;
    phase?: string;
    rank?: number;
    alignment?: number;
    role?: string;
    pct3d?: number;
    pct5d?: number;
}) {
    if (score === undefined || score === null) return null;
    const phaseMap: Record<string, string> = {
        SECTOR_EARLY: '早期',
        SECTOR_CONFIRM: '确认',
        SECTOR_CLIMAX: '高潮',
        SECTOR_FADE: '衰退',
        SECTOR_NEUTRAL: '中性',
    };
    const roleMap: Record<string, string> = {
        LEADER: '龙头',
        CORE: '中军',
        FOLLOWER: '后排',
        LAGGARD: '掉队',
    };
    const strong = score >= 75;
    const warming = score >= 58;
    return (
        <div className={cn(
            "flex flex-col items-center gap-0.5 rounded-md border px-2 py-1 text-[9px] font-black",
            strong ? "border-rose-100 bg-rose-50 text-rose-600" :
                warming ? "border-amber-100 bg-amber-50 text-amber-700" :
                    "border-slate-100 bg-slate-50 text-slate-500"
        )}>
            <span>板块{score.toFixed(0)} · {phaseMap[phase || ''] || '中性'}</span>
            <span className="font-bold opacity-80">
                扩散{breadth?.toFixed(0) ?? '--'}% · 联动{alignment?.toFixed(0) ?? '--'}{rank ? ` · #${rank}` : ''}{role ? ` · ${roleMap[role] || role}` : ''}
            </span>
            {(pct3d !== undefined || pct5d !== undefined) && (
                <span className="font-bold opacity-80">
                    3日{pct3d !== undefined ? `${pct3d >= 0 ? '+' : ''}${pct3d.toFixed(1)}%` : '--'} · 5日{pct5d !== undefined ? `${pct5d >= 0 ? '+' : ''}${pct5d.toFixed(1)}%` : '--'}
                </span>
            )}
        </div>
    );
}
