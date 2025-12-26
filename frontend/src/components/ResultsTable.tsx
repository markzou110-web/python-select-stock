"use client";

import React from 'react';
import {
    TrendingUp,
    Target,
    Map,
    BarChart3,
    Zap,
    History,
    ExternalLink,
    LayoutGrid
} from 'lucide-react';
import { cn } from '@/lib/utils';

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
    if (results.length === 0) return null;

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
                <button className="flex items-center gap-2 px-4 py-2 bg-slate-50 text-slate-500 rounded-xl text-sm font-bold hover:bg-slate-100 transition-all">
                    <ExternalLink size={16} />
                    导出选股单
                </button>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                    <thead>
                        <tr className="bg-slate-50/50">
                            <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest">股票信息</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">综合强度</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">技术指标</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">资金/行业</th>
                            <th className="px-6 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-center">历史表现</th>
                            <th className="px-8 py-4 text-[10px] font-extrabold text-slate-400 uppercase tracking-widest text-right">操作</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                        {results.map((res, i) => (
                            <tr key={res.代码} className="group hover:bg-indigo-50/30 transition-all">
                                <td className="px-8 py-5">
                                    <div className="flex items-center gap-3">
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
                                                res.北向 === "🔴流入" ? "text-rose-500" : "text-emerald-500"
                                            )}>
                                                {res.北向 || "🟢流出"}
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
                                    <button className="p-2 text-slate-300 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all">
                                        <LayoutGrid size={20} />
                                    </button>
                                    <button className="p-2 text-slate-300 hover:text-rose-600 hover:bg-rose-50 rounded-xl transition-all ml-1">
                                        <BarChart3 size={20} />
                                    </button>
                                </td>
                            </tr>
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
