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
    ChevronUp
} from 'lucide-react';
import { cn } from '@/lib/utils';
import KLineChart from './KLineChart';

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
}

export default function ResultsTable({ results }: { results: Result[] }) {
    const [expandedRow, setExpandedRow] = useState<string | null>(null);

    if (results.length === 0) return null;

    const toggleRow = (code: string) => {
        setExpandedRow(expandedRow === code ? null : code);
    };

    const openChart = (code: string) => {
        const fullCode = code.startsWith('6') || code.startsWith('688') ? `SH${code}` : `SZ${code}`;
        window.open(`https://quote.eastmoney.com/${fullCode}.html`, '_blank');
    };

    const handleExport = () => {
        const headers = ["代码", "名称", "行业", "现价", "涨幅%", "综合强度", "RSI", "DIF", "BB", "粘合度", "历史胜率"];
        const rows = results.map(r => [
            r.代码, r.名称, r.行业, r.现价, r["涨幅%"], r.Score, r.RSI, r.DIF, r.BB, r.粘合度, r.历史胜率
        ]);
        const csvContent = [headers, ...rows].map(e => e.join(",")).join("\n");
        const blob = new Blob(["\ufeff" + csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", `AlphaVision_Scan_${new Date().toLocaleDateString()}.csv`);
        link.click();
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
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">
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
                                        DIF：MACD 核心差值
                                    </div>
                                </div>
                            </th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">资金/行业</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">历史表现</th>
                            <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-right">操作</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                        {results.map((res, i) => (
                            <React.Fragment key={res.代码}>
                                <tr
                                    onClick={() => toggleRow(res.代码)}
                                    className={cn(
                                        "group transition-all cursor-pointer border-b border-slate-50",
                                        expandedRow === res.代码 ? "bg-indigo-50/50" : "hover:bg-indigo-50/20"
                                    )}
                                >
                                    <td className="px-8 py-5">
                                        <div className="flex items-center gap-3">
                                            <div className="text-slate-300 group-hover:text-indigo-400 transition-colors">
                                                {expandedRow === res.代码 ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                                            </div>
                                            <div className="flex flex-col">
                                                <span className="font-extrabold text-slate-700">{res.名称}</span>
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
                                                res["涨幅%"] >= 0 ? "text-emerald-600 bg-emerald-50" : "text-rose-600 bg-rose-50"
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
                                        <div className="flex flex-col items-center gap-2">
                                            <div className="flex items-center gap-1.5 px-3 py-1 bg-slate-100 rounded-full">
                                                <Map size={10} className="text-slate-400" />
                                                <span className="text-[10px] font-bold text-slate-600">{res.行业}</span>
                                            </div>
                                            <div className="flex items-center gap-1">
                                                <span className="text-[10px] font-bold text-slate-300">北向</span>
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
                                        </div>
                                    </td>

                                    <td className="px-8 py-5 text-right">
                                        <div className="flex justify-end gap-1">
                                            <button
                                                onClick={(e) => { e.stopPropagation(); openChart(res.代码); }}
                                                className="p-2 text-slate-300 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all"
                                                title="查看详情"
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
        </div>
    );
}

function ConfidenceBadge({ score }: { score: number }) {
    if (score > 100) return <span className="text-[9px] font-bold text-purple-500 bg-purple-50 px-2 rounded-md mt-1">💎 极高信心</span>;
    if (score > 80) return <span className="text-[9px] font-bold text-rose-500 bg-rose-50 px-2 rounded-md mt-1">🔥 强突破势</span>;
    if (score > 60) return <span className="text-[9px] font-bold text-emerald-500 bg-emerald-50 px-2 rounded-md mt-1">📊 稳定共振</span>;
    return <span className="text-[9px] font-bold text-slate-400 bg-slate-100 px-2 rounded-md mt-1">🔍 持续观察</span>;
}
