"use client";

import React, { useState } from 'react';
import { CheckCircle2, Grid3x3, Loader2, Save, TrendingUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';

interface HeatmapProps {
    code: string;
    name: string;
    strategy: string;
    onClose: () => void;
}

const PARAM_PRESETS: Record<string, { key: string; label: string; values: number[] }[]> = {
    squeeze: [
        { key: "rsi_min", label: "RSI 最低值", values: [50, 55, 60, 65] },
        { key: "stop_loss_pct", label: "止损 (%)", values: [-5, -8, -10, -12] },
        { key: "vol_multiplier", label: "量比倍数", values: [1.2, 1.5, 1.8, 2.0] },
    ],
    pine: [
        { key: "pine_min_signals", label: "最小信号数", values: [1, 2, 3, 4] },
        { key: "stop_loss_pct", label: "止损 (%)", values: [-5, -8, -10, -12] },
    ],
    consensus: [
        { key: "vol_multiplier", label: "量比倍数", values: [1.2, 1.5, 1.8, 2.0] },
        { key: "stop_loss_pct", label: "止损 (%)", values: [-5, -8, -10, -12] },
    ],
};

export default function HeatmapOptimizer({ code, name, strategy, onClose }: HeatmapProps) {
    const presets = PARAM_PRESETS[strategy] || PARAM_PRESETS.squeeze;
    const [paramXIdx, setParamXIdx] = useState(0);
    const [paramYIdx, setParamYIdx] = useState(presets.length > 1 ? 1 : 0);
    const [loading, setLoading] = useState(false);
    const [savingTemplate, setSavingTemplate] = useState(false);
    const [savedTemplate, setSavedTemplate] = useState(false);
    const [result, setResult] = useState<{
        x_labels: string[];
        y_labels: string[];
        values: number[][];
        best?: { param_x: number; param_y: number; win_rate: number };
    } | null>(null);

    const runOptimize = async () => {
        setLoading(true);
        setResult(null);
        setSavedTemplate(false);
        try {
            const res = await api.post('/api/scan/optimize', {
                code,
                strategy,
                param_x: presets[paramXIdx].key,
                param_x_values: presets[paramXIdx].values,
                param_y: presets[paramYIdx].key,
                param_y_values: presets[paramYIdx].values,
            });
            setResult(res.data);
        } catch (err) {
            console.error('Optimize error:', err);
        } finally {
            setLoading(false);
        }
    };

    const saveBestTemplate = async () => {
        if (!result?.best) return;
        setSavingTemplate(true);
        try {
            const params = {
                strategy_type: strategy,
                [presets[paramXIdx].key]: result.best.param_x,
                [presets[paramYIdx].key]: result.best.param_y,
            };
            await api.post('/api/strategy-templates/save', {
                name: `${name} 寻优参数`,
                strategy_type: strategy,
                description: `${code} 参数寻优结果：${presets[paramXIdx].label}=${result.best.param_x}，${presets[paramYIdx].label}=${result.best.param_y}，胜率 ${result.best.win_rate}%`,
                params,
            });
            setSavedTemplate(true);
        } catch (err) {
            console.error('Save template error:', err);
        } finally {
            setSavingTemplate(false);
        }
    };

    const getColor = (val: number, maxVal: number) => {
        if (maxVal === 0) return 'bg-slate-100';
        const ratio = val / maxVal;
        if (ratio >= 0.8) return 'bg-emerald-500 text-white';
        if (ratio >= 0.6) return 'bg-emerald-300 text-emerald-900';
        if (ratio >= 0.4) return 'bg-amber-200 text-amber-900';
        if (ratio >= 0.2) return 'bg-amber-100 text-amber-800';
        return 'bg-slate-50 text-slate-400';
    };

    const maxVal = result ? Math.max(...result.values.flat()) : 0;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
            <div className="bg-white rounded-2xl shadow-2xl p-8 w-[640px] max-h-[80vh] overflow-auto space-y-6 animate-in fade-in zoom-in-95 duration-200" onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between">
                    <div>
                        <h3 className="text-lg font-bold text-slate-800">参数寻优</h3>
                        <span className="text-xs text-slate-400 font-mono">{name} {code} · {strategy}</span>
                    </div>
                    <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
                </div>

                {/* 参数选择 */}
                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest block mb-1">横轴参数</label>
                        <select
                            value={paramXIdx}
                            onChange={e => setParamXIdx(Number(e.target.value))}
                            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-indigo-400"
                        >
                            {presets.map((p, i) => (
                                <option key={i} value={i}>{p.label}: [{p.values.join(', ')}]</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest block mb-1">纵轴参数</label>
                        <select
                            value={paramYIdx}
                            onChange={e => setParamYIdx(Number(e.target.value))}
                            className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-indigo-400"
                        >
                            {presets.map((p, i) => (
                                <option key={i} value={i}>{p.label}: [{p.values.join(', ')}]</option>
                            ))}
                        </select>
                    </div>
                </div>

                <button
                    onClick={runOptimize}
                    disabled={loading}
                    className="w-full py-2.5 text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                >
                    {loading ? <><Loader2 size={14} className="animate-spin" /> 计算中...</> : <><Grid3x3 size={14} /> 开始寻优</>}
                </button>

                {/* 热力图结果 */}
                {result && (
                    <div className="space-y-3">
                        {result.best && (
                            <div className="flex items-center justify-between gap-3 px-4 py-2 bg-emerald-50 border border-emerald-100 rounded-xl">
                                <div className="flex items-center gap-2 min-w-0">
                                    <TrendingUp size={14} className="text-emerald-600 shrink-0" />
                                    <span className="text-xs font-bold text-emerald-700 truncate">
                                        最佳: {presets[paramXIdx]?.label}={result.best.param_x}, {presets[paramYIdx]?.label}={result.best.param_y} → 胜率 {result.best.win_rate}%
                                    </span>
                                </div>
                                <button
                                    onClick={saveBestTemplate}
                                    disabled={savingTemplate || savedTemplate}
                                    className="px-3 py-1.5 rounded-lg bg-white text-emerald-700 border border-emerald-100 text-[11px] font-black flex items-center gap-1.5 shrink-0 disabled:opacity-70"
                                >
                                    {savingTemplate ? <Loader2 size={12} className="animate-spin" /> : savedTemplate ? <CheckCircle2 size={12} /> : <Save size={12} />}
                                    {savedTemplate ? '已保存' : '保存模板'}
                                </button>
                            </div>
                        )}

                        <div className="overflow-x-auto">
                            <table className="w-full">
                                <thead>
                                    <tr>
                                        <th className="px-2 py-1 text-[9px] font-bold text-slate-400"></th>
                                        {result.x_labels.map((l, i) => (
                                            <th key={i} className="px-2 py-1 text-[10px] font-bold text-slate-500 text-center">{l}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {result.values.map((row, yi) => (
                                        <tr key={yi}>
                                            <td className="px-2 py-1 text-[10px] font-bold text-slate-500 text-right">{result.y_labels[yi]}</td>
                                            {row.map((val, xi) => (
                                                <td key={xi} className="px-1 py-1">
                                                    <div className={cn(
                                                        "px-2 py-3 rounded-lg text-center text-xs font-bold transition-all",
                                                        getColor(val, maxVal),
                                                        val === maxVal && val > 0 && "ring-2 ring-emerald-600"
                                                    )}>
                                                        {val}%
                                                    </div>
                                                </td>
                                            ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
