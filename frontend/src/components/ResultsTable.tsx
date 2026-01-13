"use client";

import React, { useState } from 'react';
import {
    Target,
    Map,
    BarChart3,
    History,
    ExternalLink,
    HelpCircle,
    ChevronDown,
    ChevronUp,
    AlertTriangle,
    Lock,
    Calendar,
    Plus,
    Calculator as CalcIcon,
    Newspaper
} from 'lucide-react';
import { cn } from '@/lib/utils';
import KLineChart from './KLineChart';
import PositionSizer from './PositionSizer';
import NewsDetailModal from './NewsDetailModal';
import api from '@/lib/api';

interface Result {
    代码: string;
    名称: string;
    行业: string;
    现价: number;
    "涨幅%": number;
    Score: number;
    RSI: number;
    DIF: number;
    BB: number;
    粘合度: number;
    历史胜率: string;
    信号次数: number;
    北向?: string;
    共振?: string;
    影线比?: number;
    PE?: number;
    换手率?: number;
    量比?: number;
    warnings?: string[];
}

export default function ResultsTable({
    results,
    onSelectStock,
    selectedCode
}: {
    results: Result[],
    onSelectStock?: (stock: Result) => void,
    selectedCode?: string
}) {
    const [expandedRow, setExpandedRow] = useState<string | null>(null);
    const [sizingStock, setSizingStock] = useState<Result | null>(null);
    const [newsStock, setNewsStock] = useState<Result | null>(null);
    const [sortConfig, setSortConfig] = useState<{ key: keyof Result; direction: 'asc' | 'desc' } | null>(null);

    if (results.length === 0) return null;

    const sortedResults = [...results].sort((a, b) => {
        if (!sortConfig) return 0;
        const aValue = a[sortConfig.key];
        const bValue = b[sortConfig.key];

        if (aValue === undefined || bValue === undefined) return 0;

        if (aValue < bValue) return sortConfig.direction === 'asc' ? -1 : 1;
        if (aValue > bValue) return sortConfig.direction === 'asc' ? 1 : -1;
        return 0;
    });

    const requestSort = (key: keyof Result) => {
        let direction: 'asc' | 'desc' = 'desc';
        if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') {
            direction = 'asc';
        }
        setSortConfig({ key, direction });
    };

    const SortIcon = ({ columnKey }: { columnKey: keyof Result }) => {
        if (!sortConfig || sortConfig.key !== columnKey) return null;
        return sortConfig.direction === 'asc' ? <ChevronUp size={10} className="inline ml-1" /> : <ChevronDown size={10} className="inline ml-1" />;
    };

    const toggleRow = (code: string) => {
        setExpandedRow(expandedRow === code ? null : code);
    };

    const openChart = (code: string) => {
        const fullCode = code.startsWith('6') || code.startsWith('688') ? `SH${code}` : `SZ${code}`;
        window.open(`https://quote.eastmoney.com/${fullCode}.html`, '_blank');
    };

    const handleExport = () => {
        // ...Existing export logic
    };

    const addToWatchlist = async (stock: Result) => {
        try {
            await api.post('/api/paper/add', {
                code: stock.代码,
                name: stock.名称,
                price: stock.现价
            });
            alert(`${stock.名称} 已加入模拟池！`);
        } catch (err) {
            console.error(err);
        }
    };

    return (
        <div className="glass-card overflow-hidden border-none shadow-2xl shadow-slate-200/50 animate-in fade-in slide-in-from-bottom-4 duration-500">
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
                <button
                    onClick={handleExport}
                    className="flex items-center gap-2 px-4 py-2 bg-slate-50 text-slate-500 rounded-xl text-sm font-bold hover:bg-slate-100 transition-all"
                >
                    <ExternalLink size={16} />
                    导出选股单
                </button>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                    <thead>
                        <tr className="bg-slate-50/50">
                            <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest">股票信息</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center cursor-pointer hover:bg-slate-100" onClick={() => requestSort('Score')}>
                                <div className="flex items-center justify-center gap-1 group/tooltip relative">
                                    综合强度
                                    <SortIcon columnKey="Score" />
                                    <HelpCircle size={10} />
                                    {/* ...Tooltip... */}
                                </div>
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">
                                技术指标
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center cursor-pointer hover:bg-slate-100" onClick={() => requestSort('影线比')}>
                                价格形态 <SortIcon columnKey="影线比" />
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">
                                价值/流量
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">所属板块</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">历史表现</th>
                            <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-right">操作</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                        {sortedResults.map((res, i) => (
                            <React.Fragment key={res.代码}>
                                <tr
                                    onClick={() => {
                                        onSelectStock?.(res);
                                        toggleRow(res.代码);
                                    }}
                                    className={cn(
                                        "group transition-all cursor-pointer border-b border-slate-50",
                                        expandedRow === res.代码 ? "bg-indigo-50/50" : "hover:bg-indigo-50/20",
                                        selectedCode === res.代码 && "bg-indigo-50/50 ring-1 ring-inset ring-indigo-100"
                                    )}
                                >
                                    <td className="px-8 py-5">
                                        <div className="flex items-center gap-3">
                                            <div className="text-slate-400 transition-colors">
                                                {expandedRow === res.代码 ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                                            </div>
                                            <div className="flex flex-col">
                                                <div className="flex items-center gap-1.5">
                                                    <span className="font-extrabold text-slate-700">{res.名称}</span>
                                                    {res.warnings && res.warnings.length > 0 && (
                                                        <div className="flex gap-1">
                                                            {res.warnings.includes("📅 财报") && <Calendar size={10} className="text-amber-500 animate-pulse" />}
                                                            {res.warnings.includes("🔒 解禁") && <Lock size={10} className="text-rose-500" />}
                                                            {res.warnings.includes("⚠️ 减持") && <AlertTriangle size={10} className="text-rose-600" />}
                                                        </div>
                                                    )}
                                                </div>
                                                <span className="text-[10px] font-mono font-bold text-slate-400 tracking-tighter">{res.代码}</span>
                                            </div>
                                        </div>
                                    </td>

                                    <td className="px-6 py-5">
                                        <div className="flex flex-col items-center">
                                            <div className="flex items-baseline gap-1">
                                                <span className="text-lg font-black text-slate-800">{res.Score.toFixed(1)}</span>
                                                <span className="text-[10px] font-bold text-indigo-500">PT</span>
                                            </div>
                                            <ConfidenceBadge score={res.Score} />
                                        </div>
                                    </td>

                                    <td className="px-6 py-5">
                                        <div className="flex flex-col items-center gap-2">
                                            <div className={cn(
                                                "text-sm font-bold px-2 py-0.5 rounded-lg",
                                                res["涨幅%"] >= 0 ? "text-rose-600 bg-rose-50" : "text-emerald-600 bg-emerald-50"
                                            )}>
                                                {res["涨幅%"] >= 0 ? '+' : ''}{res["涨幅%"].toFixed(2)}%
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

                                    <td className="px-6 py-5">
                                        <div className="flex flex-col items-center gap-1">
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
                                            <span className="text-[9px] text-slate-400 font-bold uppercase tracking-tighter">影线/实体</span>
                                        </div>
                                    </td>

                                    <td className="px-6 py-5">
                                        <div className="flex flex-col items-center gap-2">
                                            <div className="flex items-center gap-1.5 px-3 py-1 bg-indigo-50 text-indigo-600 border border-indigo-100 rounded-full min-w-[60px] justify-center">
                                                <Map size={10} className="text-indigo-400" />
                                                <span className="text-[10px] font-black break-keep whitespace-nowrap">
                                                    {(res.行业 && res.行业.trim()) ? res.行业 : "未知"}
                                                </span>
                                            </div>
                                            {res.共振 === "🔥 核心热点" && (
                                                <div className="flex items-center gap-1.5 px-2.5 py-1 bg-indigo-600 text-white rounded-lg shadow-lg shadow-indigo-100 animate-pulse">
                                                    <span className="text-[9px] font-black uppercase tracking-tighter">🔥 板块共振</span>
                                                </div>
                                            )}
                                        </div>
                                    </td>

                                    <td className="px-6 py-5">
                                        <div className="flex flex-col items-center gap-1">
                                            <div className="flex items-center gap-2">
                                                <div className="flex flex-col items-center cursor-pointer hover:bg-slate-50 px-1 rounded" onClick={(e) => { e.stopPropagation(); requestSort('量比'); }}>
                                                    <span className="text-[9px] font-bold text-slate-300">量比</span>
                                                    <span className="text-[11px] font-black text-indigo-600">{res.量比 || '---'}</span>
                                                </div>
                                                <div className="flex flex-col items-center cursor-pointer hover:bg-slate-50 px-1 rounded" onClick={(e) => { e.stopPropagation(); requestSort('换手率'); }}>
                                                    <span className="text-[9px] font-bold text-slate-300">换手</span>
                                                    <span className="text-[11px] font-black text-slate-600">{res.换手率 || '---'}%</span>
                                                </div>
                                                <div className="flex flex-col items-center cursor-pointer hover:bg-slate-50 px-1 rounded" onClick={(e) => { e.stopPropagation(); requestSort('PE'); }}>
                                                    <span className="text-[9px] font-bold text-slate-300">PE</span>
                                                    <span className="text-[11px] font-black text-slate-600">{res.PE || '---'}</span>
                                                </div>
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
                                        </div>
                                    </td>

                                    <td className="px-8 py-5 text-right">
                                        <div className="flex items-center justify-end gap-2">
                                            <button
                                                onClick={(e) => { e.stopPropagation(); setNewsStock(res); }}
                                                className="p-2 text-slate-400 hover:text-orange-600 hover:bg-orange-50 rounded-xl transition-all"
                                                title="相关新闻"
                                            >
                                                <Newspaper size={18} />
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); setSizingStock(res); }}
                                                className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all"
                                                title="仓位计算"
                                            >
                                                <CalcIcon size={18} />
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); addToWatchlist(res); }}
                                                className="p-2 text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 rounded-xl transition-all"
                                                title="加入模拟池"
                                            >
                                                <Plus size={18} />
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); openChart(res.代码); }}
                                                className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-slate-100 rounded-xl transition-all"
                                                title="详情"
                                            >
                                                <ExternalLink size={18} />
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); toggleRow(res.代码); }}
                                                className={cn(
                                                    "p-2 rounded-xl transition-all",
                                                    expandedRow === res.代码 ? "text-indigo-600 bg-indigo-50" : "text-slate-300 hover:text-rose-600 hover:bg-rose-50"
                                                )}
                                                title="查看K线"
                                            >
                                                <BarChart3 size={18} />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                                {expandedRow === res.代码 && (
                                    <tr className="bg-slate-50/30 animate-in fade-in slide-in-from-top-2 duration-300">
                                        <td colSpan={6} className="px-8 py-6">
                                            <div className="flex flex-col gap-4">
                                                <div className="flex items-center justify-between">
                                                    <div className="flex items-center gap-3">
                                                        <div className="px-3 py-1 bg-white border border-slate-200 rounded-lg text-xs font-bold text-slate-600 shadow-sm">
                                                            📈 动态 K 线集成
                                                        </div>
                                                        <span className="text-xs text-slate-400 font-bold font-mono tracking-widest">{res.名称} {res.代码}</span>
                                                    </div>
                                                    <div className="flex gap-4">
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); openChart(res.代码); }}
                                                            className="flex items-center gap-1.5 text-xs font-bold text-indigo-600 hover:text-indigo-700 transition-colors"
                                                        >
                                                            <ExternalLink size={12} />
                                                            东财详情
                                                        </button>
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); toggleRow(res.代码); }}
                                                            className="text-xs font-bold text-slate-400 hover:text-slate-600 transition-colors"
                                                        >
                                                            收起图表
                                                        </button>
                                                    </div>
                                                </div>
                                                <div className="w-full h-[450px] bg-white rounded-2xl border border-slate-100 shadow-xl overflow-hidden relative group/chart">
                                                    <KLineChart code={res.代码} name={res.名称} />
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
                        ))}
                    </tbody>
                </table>
            </div>

            {sizingStock && (
                <PositionSizer
                    stock={sizingStock}
                    onClose={() => setSizingStock(null)}
                />
            )}

            {newsStock && (
                <NewsDetailModal
                    isOpen={!!newsStock}
                    onClose={() => setNewsStock(null)}
                    stockCode={newsStock.代码}
                    stockName={newsStock.名称}
                />
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
