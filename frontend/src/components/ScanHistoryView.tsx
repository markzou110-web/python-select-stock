import React, { useState, useEffect } from 'react';
import { Calendar, ChevronRight, TrendingUp, TrendingDown, RefreshCw, BarChart2 } from 'lucide-react';
import { clampScore, cn } from '@/lib/utils';
import api from '@/lib/api';
import type { ScanResult } from '@/stores/scanStore';

type HistoryResult = ScanResult & { 最新价?: number; '表现%'?: number };

interface ScanHistoryViewProps {
    availableDates: string[];
}

export default function ScanHistoryView({ availableDates }: ScanHistoryViewProps) {
    const [selectedDate, setSelectedDate] = useState<string>(availableDates[0] || '');
    const [historyData, setHistoryData] = useState<HistoryResult[]>([]);
    const [isLoading, setIsLoading] = useState(false);

    useEffect(() => {
        if (!selectedDate) return;
        const fetchHistory = async () => {
            setIsLoading(true);
            try {
                const res = await api.get(`/api/scan/history?date=${selectedDate}`);
                setHistoryData(res.data);
            } catch (e) {
                console.error("Failed to fetch history:", e);
            } finally {
                setIsLoading(false);
            }
        };
        fetchHistory();
    }, [selectedDate]);

    // 计算总表现
    const winCount = historyData.filter(d => (d['表现%'] || 0) > 0).length;
    const avgPerformance = historyData.length > 0 
        ? historyData.reduce((acc, curr) => acc + (curr['表现%'] || 0), 0) / historyData.length 
        : 0;

    return (
        <div className="flex flex-col gap-6">
            <div className="flex flex-col md:flex-row gap-6">
                {/* 左侧：日期列表 */}
                <div className="w-full md:w-64 flex-shrink-0">
                    <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden flex flex-col h-[600px]">
                        <div className="p-4 border-b border-slate-100 bg-slate-50 flex items-center gap-2">
                            <Calendar size={18} className="text-indigo-500" />
                            <h3 className="font-bold text-slate-700">历史回溯</h3>
                        </div>
                        <div className="flex-1 overflow-y-auto p-2 space-y-1">
                            {availableDates.map(date => (
                                <button
                                    key={date}
                                    onClick={() => setSelectedDate(date)}
                                    className={cn(
                                        "w-full text-left px-3 py-2.5 rounded-xl text-sm font-medium transition-all flex items-center justify-between",
                                        selectedDate === date 
                                            ? "bg-indigo-50 text-indigo-700 font-bold" 
                                            : "text-slate-600 hover:bg-slate-50"
                                    )}
                                >
                                    <span>{date}</span>
                                    {selectedDate === date && <ChevronRight size={16} />}
                                </button>
                            ))}
                            {availableDates.length === 0 && (
                                <p className="text-center text-slate-400 text-sm font-bold mt-10">暂无扫描记录</p>
                            )}
                        </div>
                    </div>
                </div>

                {/* 右侧：结果详情 */}
                <div className="flex-1 bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden flex flex-col h-[600px]">
                    <div className="p-4 border-b border-slate-100 bg-slate-50 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <h3 className="font-bold text-slate-700 text-lg">
                                {selectedDate} <span className="text-slate-400 font-normal text-sm ml-2">选出 {historyData.length} 只标的</span>
                            </h3>
                        </div>
                        {historyData.length > 0 && (
                            <div className="flex items-center gap-4 text-sm font-medium bg-white px-4 py-1.5 rounded-full border border-slate-200">
                                <div className="flex items-center gap-1.5">
                                    <span className="text-slate-400">平均表现:</span>
                                    <span className={cn(avgPerformance >= 0 ? "text-rose-500" : "text-emerald-500", "font-bold")}>
                                        {avgPerformance >= 0 ? '+' : ''}{avgPerformance.toFixed(2)}%
                                    </span>
                                </div>
                                <div className="w-[1px] h-4 bg-slate-200" />
                                <div className="flex items-center gap-1.5">
                                    <span className="text-slate-400">上涨比例:</span>
                                    <span className="text-indigo-600 font-bold">
                                        {Math.round((winCount / historyData.length) * 100)}%
                                    </span>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="flex-1 overflow-y-auto">
                        {isLoading ? (
                            <div className="flex flex-col items-center justify-center h-full text-slate-400 space-y-3">
                                <RefreshCw className="animate-spin text-indigo-400" size={32} />
                                <p className="font-medium text-sm">加载回测数据中...</p>
                            </div>
                        ) : historyData.length > 0 ? (
                            <table className="w-full text-left text-sm whitespace-nowrap">
                                <thead className="bg-slate-50/80 sticky top-0 z-10 backdrop-blur-sm border-b border-slate-100">
                                    <tr className="text-slate-400 font-medium text-[11px] uppercase tracking-wider">
                                        <th className="px-4 py-3">标的</th>
                                        <th className="px-3 py-3">板块</th>
                                        <th className="px-3 py-3">选出价</th>
                                        <th className="px-3 py-3">当前价</th>
                                        <th className="px-3 py-3 text-right">至今表现</th>
                                        <th className="px-3 py-3 text-right">质量分</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {historyData.map(res => (
                                        <tr key={res['代码']} className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors">
                                            <td className="px-4 py-3">
                                                <div className="flex flex-col">
                                                    <span className="font-bold text-slate-700">{res['名称']}</span>
                                                    <span className="text-[10px] text-slate-400 font-mono">{res['代码']}</span>
                                                </div>
                                            </td>
                                            <td className="px-3 py-3">
                                                <span className="px-2 py-1 bg-slate-100 text-slate-600 rounded-md text-[10px] font-medium">
                                                    {res['行业'] || '未知'}
                                                </span>
                                            </td>
                                            <td className="px-3 py-3 font-mono text-slate-500">¥{res['现价']?.toFixed(2)}</td>
                                            <td className="px-3 py-3 font-mono font-bold text-slate-700">¥{res['最新价']?.toFixed(2)}</td>
                                            <td className="px-3 py-3 text-right">
                                                <div className="flex items-center justify-end gap-1 font-bold font-mono">
                                                    {(res['表现%'] || 0) > 0 ? (
                                                        <TrendingUp size={14} className="text-rose-500" />
                                                    ) : (res['表现%'] || 0) < 0 ? (
                                                        <TrendingDown size={14} className="text-emerald-500" />
                                                    ) : <BarChart2 size={14} className="text-slate-400" />}
                                                    <span className={(res['表现%'] || 0) > 0 ? "text-rose-500" : (res['表现%'] || 0) < 0 ? "text-emerald-500" : "text-slate-500"}>
                                                        {(res['表现%'] || 0) > 0 ? '+' : ''}{(res['表现%'] || 0).toFixed(2)}%
                                                    </span>
                                                </div>
                                            </td>
                                            <td className="px-3 py-3 text-right">
                                                <span className="font-bold text-indigo-600 bg-indigo-50 px-2 py-1 rounded-lg">
                                                    {clampScore(res['display_signal_score'] ?? res['Score']).toFixed(1)}/100
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        ) : (
                            <div className="flex flex-col items-center justify-center h-full text-slate-400 space-y-2">
                                <BarChart2 size={32} className="opacity-50" />
                                <p className="text-sm">该日期无扫描数据</p>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
