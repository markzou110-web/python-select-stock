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
    Calculator as CalcIcon
} from 'lucide-react';
import { cn } from '@/lib/utils';
import dynamic from 'next/dynamic';
const KLineChart = dynamic(() => import('./KLineChart'), { ssr: false, loading: () => <div className="h-48 flex items-center justify-center text-slate-400 text-xs">Loading chart...</div> });
import PositionSizer from './PositionSizer';
import api from '@/lib/api';
import { ScanResult } from '@/stores/scanStore';
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
    const [remarkStock, setRemarkStock] = useState<ScanResult | null>(null);
    const [remarkText, setRemarkText] = useState('');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 2500);
    };

    if (results.length === 0) return null;

    const toggleRow = (code: string) => {
        setExpandedRow(expandedRow === code ? null : code);
    };

    const openChart = (code: string) => {
        const fullCode = code.startsWith('6') || code.startsWith('688') ? `SH${code}` : `SZ${code}`;
        window.open(`https://quote.eastmoney.com/${fullCode}.html`, '_blank');
    };

    const handleExport = () => {
        const headers = ['代码', '名称', '行业', '现价', '涨幅%', 'Score', 'RSI', 'DIF', 'BB', '粘合度', 'ROE', '净利YOY', '历史胜率', '信号次数', '北向', '共振', '影线比', 'strategy_type'];
        const rows = results.map(r => [
            r.代码, r.名称, r.行业, r.现价, r['涨幅%'], r.Score, r.RSI, r.DIF, r.BB,
            r.粘合度, r.ROE || '', r.净利YOY || '', r.历史胜率, r.信号次数, r.北向 || '', r.共振 || '',
            r.影线比 || '', r.strategy_type || ''
        ]);

        // BOM for Excel UTF-8 compatibility
        const csv = '\uFEFF' + [headers, ...rows].map(row =>
            row.map(cell => {
                const str = String(cell ?? '');
                return str.includes(',') || str.includes('"') || str.includes('\n')
                    ? `"${str.replace(/"/g, '""')}"`
                    : str;
            }).join(',')
        ).join('\n');

        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const date = new Date().toISOString().slice(0, 10);
        a.href = url;
        a.download = `AlphaVision_选股_${date}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const addToWatchlist = async (stock: ScanResult, remark?: string) => {
        try {
            await api.post('/api/paper/add', {
                code: stock.代码,
                name: stock.名称,
                price: stock.现价,
                strategy_type: stock.strategy_type,
                remark: remark || undefined
            });
            showToast(`${stock.名称} 已加入模拟池`);
        } catch (err) {
            console.error(err);
            showToast('加入失败，请重试', 'error');
        }
    };

    const confirmAddToWatchlist = () => {
        if (remarkStock) {
            addToWatchlist(remarkStock, remarkText);
            setRemarkStock(null);
            setRemarkText('');
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
                        {results.map((res, i) => (
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
                                            {res.结构 && (
                                                <div className="px-2 py-0.5 bg-blue-50 text-blue-600 rounded-lg text-[10px] font-black border border-blue-100 mb-0.5">
                                                    {res.结构}
                                                </div>
                                            )}
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
                                            {res.ROE !== undefined && res.ROE !== null ? (
                                                <div className="flex flex-col items-center gap-1">
                                                    <div className="flex gap-2">
                                                        <div className="flex flex-col items-center">
                                                            <span className="text-[9px] font-bold text-slate-300">ROE</span>
                                                            <span className={cn("text-[10px] font-extrabold", res.ROE >= 15 ? "text-rose-500" : res.ROE >= 8 ? "text-orange-500" : "text-slate-500")}>{res.ROE}%</span>
                                                        </div>
                                                        <div className="flex flex-col items-center">
                                                            <span className="text-[9px] font-bold text-slate-300">净利YOY</span>
                                                            <span className={cn("text-[10px] font-extrabold", res.净利YOY >= 30 ? "text-rose-500" : res.净利YOY >= 15 ? "text-orange-500" : "text-slate-500")}>{res.净利YOY}%</span>
                                                        </div>
                                                    </div>
                                                    {(res.ROE >= 15 || res.净利YOY >= 30) && (
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
                                        <td colSpan={9} className="px-8 py-6">
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
                                                    <KLineChart code={res.代码} name={res.名称} strategyType={res.strategy_type || 'squeeze'} />
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

            {remarkStock && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={() => { setRemarkStock(null); setRemarkText(''); }}>
                    <div className="bg-white rounded-2xl shadow-2xl p-6 w-[400px] space-y-4 animate-in fade-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-bold text-slate-800">加入模拟池</h3>
                            <span className="text-xs text-slate-400 font-mono">{remarkStock.名称} {remarkStock.代码}</span>
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
                                onClick={() => { setRemarkStock(null); setRemarkText(''); }}
                                className="px-4 py-2 text-xs font-bold text-slate-400 hover:text-slate-600 rounded-xl transition-all"
                            >
                                取消
                            </button>
                            <button
                                onClick={confirmAddToWatchlist}
                                className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl transition-all"
                            >
                                确认加入
                            </button>
                        </div>
                    </div>
                </div>
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
