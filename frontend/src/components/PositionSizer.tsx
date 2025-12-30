"use client";

import React, { useState, useEffect } from 'react';
import {
    X,
    Calculator,
    ShieldAlert,
    TrendingDown,
    Target,
    ArrowRight
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface PositionSizerProps {
    stock: any;
    onClose: () => void;
}

export default function PositionSizer({ stock, onClose }: PositionSizerProps) {
    const [totalCapital, setTotalCapital] = useState(500000); // 默认 50万
    const [riskPercent, setRiskPercent] = useState(2); // 默认 2%
    const [stopLossPrice, setStopLossPrice] = useState(0);

    // 自动计算止损价：默认设为当前价的 -5%
    useEffect(() => {
        if (stock?.现价) {
            setStopLossPrice(Number((stock.现价 * 0.95).toFixed(2)));
        }
    }, [stock]);

    const riskAmount = totalCapital * (riskPercent / 100);
    const lossPerShare = stock.现价 - stopLossPrice;
    const recommendedShares = lossPerShare > 0 ? Math.floor(riskAmount / lossPerShare) : 0;
    // A股一手为100股
    const recommendedLots = Math.floor(recommendedShares / 100);
    const totalCost = recommendedLots * 100 * stock.现价;

    return (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-[60] flex items-center justify-center p-4">
            <div className="bg-white rounded-3xl w-full max-w-md shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
                {/* Header */}
                <div className="p-6 border-b border-slate-100 flex items-center justify-between">
                    <div className="flex items-center gap-3 text-indigo-600">
                        <div className="w-10 h-10 bg-indigo-50 rounded-xl flex items-center justify-center">
                            <Calculator size={20} />
                        </div>
                        <div>
                            <h3 className="font-bold text-slate-900">仓位计算器</h3>
                            <p className="text-[10px] text-slate-400 font-bold tracking-widest uppercase">{stock.名称} ({stock.代码})</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-slate-100 rounded-xl transition-all text-slate-400">
                        <X size={20} />
                    </button>
                </div>

                {/* Content */}
                <div className="p-8 space-y-6">
                    {/* Inputs */}
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-[11px] font-black text-slate-400 uppercase">总资金 (元)</label>
                            <input
                                type="number"
                                value={totalCapital}
                                onChange={(e) => setTotalCapital(Number(e.target.value))}
                                className="w-full bg-slate-50 border border-slate-100 rounded-xl px-4 py-3 font-mono text-sm focus:ring-2 focus:ring-indigo-500/20 outline-none transition-all"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-[11px] font-black text-slate-400 uppercase">单笔风险 (%)</label>
                            <input
                                type="number"
                                value={riskPercent}
                                onChange={(e) => setRiskPercent(Number(e.target.value))}
                                className="w-full bg-slate-50 border border-slate-100 rounded-xl px-4 py-3 font-mono text-sm focus:ring-2 focus:ring-indigo-500/20 outline-none transition-all"
                            />
                        </div>
                    </div>

                    <div className="p-5 bg-rose-50 rounded-2xl border border-rose-100 space-y-3">
                        <div className="flex items-center justify-between">
                            <label className="text-xs font-bold text-rose-600 flex items-center gap-1.5">
                                <TrendingDown size={14} /> 止损价 (元)
                            </label>
                            <span className="text-[10px] text-rose-400 font-medium">当前价 {stock.现价}</span>
                        </div>
                        <input
                            type="number"
                            value={stopLossPrice}
                            onChange={(e) => setStopLossPrice(Number(e.target.value))}
                            step="0.01"
                            className="w-full bg-white border border-rose-200 rounded-xl px-4 py-3 font-black text-lg text-rose-600 focus:ring-4 focus:ring-rose-500/10 outline-none transition-all"
                        />
                    </div>

                    {/* Results Overlay */}
                    <div className="bg-slate-900 rounded-2xl p-6 text-white relative overflow-hidden">
                        <div className="relative z-10 space-y-4">
                            <div className="flex items-center justify-between border-b border-white/10 pb-4">
                                <span className="text-xs font-medium opacity-60">建议购入</span>
                                <div className="text-right">
                                    <div className="text-2xl font-black text-amber-400">{recommendedLots * 100} <span className="text-xs opacity-60 ml-1">股</span></div>
                                    <div className="text-[10px] font-bold opacity-40 uppercase tracking-tighter">({recommendedLots} 手)</div>
                                </div>
                            </div>
                            <div className="flex items-center justify-between border-b border-white/10 pb-4">
                                <span className="text-xs font-medium opacity-60">单笔风险金额</span>
                                <span className="font-bold text-rose-400">¥{riskAmount.toLocaleString()}</span>
                            </div>
                            <div className="flex items-center justify-between">
                                <span className="text-xs font-medium opacity-60">预计成交额</span>
                                <span className="font-black">¥{totalCost.toLocaleString()}</span>
                            </div>
                        </div>
                        <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/10 rounded-full -translate-y-1/2 translate-x-1/2 blur-2xl" />
                    </div>

                    <div className="flex items-start gap-3 p-4 bg-indigo-50 rounded-xl border border-indigo-100 text-[11px] text-indigo-600 leading-relaxed font-medium">
                        <ShieldAlert size={16} className="shrink-0" />
                        <p>
                            风险提示：若股价跌破 {stopLossPrice} 元，您将损失约 ¥{Math.round((stock.现价 - stopLossPrice) * recommendedLots * 100)} 元，不超过总资金的 {riskPercent}%。
                        </p>
                    </div>
                </div>

                {/* Footer */}
                <div className="p-6 bg-slate-50 border-t border-slate-100">
                    <button
                        onClick={onClose}
                        className="w-full py-4 premium-gradient text-white rounded-2xl font-black shadow-xl shadow-indigo-100 hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center justify-center gap-2"
                    >
                        确认执行计划
                        <ArrowRight size={18} />
                    </button>
                </div>
            </div>
        </div>
    );
}
