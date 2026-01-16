"use client";

import React from 'react';
import { X, Check } from 'lucide-react';
import { cn } from '@/lib/utils';

interface FilterModalProps {
    isOpen: boolean;
    onClose: () => void;
    params: any;
    setParams: (params: any) => void;
    onScan: () => void;
}

export default function FilterModal({ isOpen, onClose, params, setParams, onScan }: FilterModalProps) {
    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
            <div className="bg-white rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
                <div className="flex items-center justify-between px-8 py-6 border-b border-slate-100 bg-slate-50/50">
                    <div>
                        <h3 className="text-xl font-bold text-slate-800">🔬 高级策略筛选</h3>
                        <p className="text-xs text-slate-400 font-medium mt-0.5 uppercase tracking-wider">TradingView Pro Logic Configuration</p>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-slate-200 rounded-xl transition-colors">
                        <X size={20} className="text-slate-500" />
                    </button>
                </div>

                <div className="p-8 space-y-8">
                    {/* Strategy Selection */}
                    <div className="bg-slate-50 p-6 rounded-3xl border border-slate-100">
                        <label className="text-xs font-extrabold text-slate-500 uppercase tracking-wider mb-3 block">选择核心选股策略</label>
                        <div className="grid grid-cols-2 gap-4">
                            <button
                                onClick={() => setParams({ ...params, strategy: "Resonance" })}
                                className={cn(
                                    "px-4 py-3 rounded-2xl font-bold transition-all text-xs flex flex-col gap-1 border-2",
                                    params.strategy === "Resonance"
                                        ? "bg-indigo-600 text-white border-indigo-600 shadow-lg shadow-indigo-100"
                                        : "bg-white text-slate-600 border-slate-100 hover:border-slate-200"
                                )}
                            >
                                <span>🎯 无门问禅</span>
                                <span className={cn("text-[9px] font-medium", params.strategy === "Resonance" ? "text-indigo-100" : "text-slate-400")}>核心多因子共振突破</span>
                            </button>
                            <button
                                onClick={() => setParams({ ...params, strategy: "Range Filter" })}
                                className={cn(
                                    "px-4 py-3 rounded-2xl font-bold transition-all text-xs flex flex-col gap-1 border-2",
                                    params.strategy === "Range Filter"
                                        ? "bg-indigo-600 text-white border-indigo-600 shadow-lg shadow-indigo-100"
                                        : "bg-white text-slate-600 border-slate-100 hover:border-slate-200"
                                )}
                            >
                                <span>🎢 Range Filter</span>
                                <span className={cn("text-[9px] font-medium", params.strategy === "Range Filter" ? "text-indigo-100" : "text-slate-400")}>波动率趋势追踪</span>
                            </button>
                            <button
                                onClick={() => setParams({ ...params, strategy: "Combined" })}
                                className={cn(
                                    "px-4 py-3 rounded-2xl font-bold transition-all text-xs flex flex-col gap-1 border-2 col-span-2 sm:col-span-1",
                                    params.strategy === "Combined"
                                        ? "bg-rose-600 text-white border-rose-600 shadow-lg shadow-rose-100"
                                        : "bg-white text-slate-600 border-slate-100 hover:border-slate-200"
                                )}
                            >
                                <span>⚔️ 双重策略</span>
                                <span className={cn("text-[9px] font-medium", params.strategy === "Combined" ? "text-rose-100" : "text-slate-400")}>共振 + 过滤 (交集)</span>
                            </button>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-8">
                        {/* Column 1 */}
                        <div className="space-y-6">
                            {(params.strategy === "Range Filter" || params.strategy === "Combined") && (
                                <>
                                    <FilterItem label="RF 周期 (Period: 10~200)">
                                        <input
                                            type="number" value={isNaN(params.rf_period) ? "" : params.rf_period}
                                            onChange={e => setParams({ ...params, rf_period: parseInt(e.target.value) || 0 })}
                                            className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                        />
                                    </FilterItem>
                                    <FilterItem label="RF 倍数 (Multiplier: 1.0~5.0)">
                                        <input
                                            type="number" step="0.1" value={isNaN(params.rf_multiplier) ? "" : params.rf_multiplier}
                                            onChange={e => setParams({ ...params, rf_multiplier: parseFloat(e.target.value) || 0 })}
                                            className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                        />
                                    </FilterItem>
                                </>
                            )}
                            {(params.strategy === "Resonance" || params.strategy === "Combined") && (
                                <>
                                    <FilterItem label="粘合度阈值 (0.01~0.30)">
                                        <input
                                            type="range" min="0.01" max="0.30" step="0.01" value={params.threshold}
                                            onChange={e => setParams({ ...params, threshold: parseFloat(e.target.value) })}
                                            className="w-full accent-indigo-600"
                                        />
                                        <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                            <span>极限粘合</span>
                                            <span className="text-indigo-600 font-extrabold text-xs">{params.threshold.toFixed(2)}</span>
                                            <span>宽容粘合</span>
                                        </div>
                                    </FilterItem>

                                    <FilterItem label="量比倍数 (1.0~5.0)">
                                        <input
                                            type="number" step="0.1" value={isNaN(params.vol_multiplier) ? "" : params.vol_multiplier}
                                            onChange={e => setParams({ ...params, vol_multiplier: parseFloat(e.target.value) || 0 })}
                                            className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                        />
                                    </FilterItem>
                                </>
                            )}

                            <div className="space-y-3 pt-2">
                                <ToggleItem
                                    label="启用周线趋势过滤"
                                    active={params.use_weekly}
                                    onClick={() => setParams({ ...params, use_weekly: !params.use_weekly })}
                                />
                                {(params.strategy === "Resonance" || params.strategy === "Combined") && (
                                    <>
                                        <ToggleItem
                                            label="🔥 MACD 必须处于金叉 (红柱)"
                                            active={params.use_macd_filter}
                                            onClick={() => setParams({ ...params, use_macd_filter: !params.use_macd_filter })}
                                        />
                                        <label className="flex items-center gap-3 px-4 py-3 rounded-xl border-2 border-slate-100 cursor-pointer transition-all hover:bg-slate-50">
                                            <span className="flex-1 font-semibold text-slate-600">相对强度过滤 (RS)</span>
                                            <input
                                                type="checkbox" checked={params.use_rs_filter}
                                                onChange={e => setParams({ ...params, use_rs_filter: e.target.checked })}
                                                className="w-5 h-5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                                            />
                                        </label>
                                    </>
                                )}
                                <FilterItem label="最小换手率 (%)">
                                    <input
                                        type="number" step="0.5" value={isNaN(params.turnover_min) ? "" : params.turnover_min}
                                        onChange={e => setParams({ ...params, turnover_min: parseFloat(e.target.value) || 0 })}
                                        className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                    />
                                </FilterItem>
                                <FilterItem label="最小市值 (亿)">
                                    <input
                                        type="number" step="10" value={isNaN(params.mkt_cap_min) ? "" : params.mkt_cap_min}
                                        onChange={e => setParams({ ...params, mkt_cap_min: parseFloat(e.target.value) || 0 })}
                                        className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                    />
                                </FilterItem>
                            </div>
                        </div>

                        {/* Column 2 */}
                        <div className="space-y-6">
                            <FilterItem label="RSI 最小强度 (30~80)">
                                <input
                                    type="number" value={isNaN(params.rsi_min) ? "" : params.rsi_min}
                                    onChange={e => setParams({ ...params, rsi_min: parseInt(e.target.value) || 0 })}
                                    className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                />
                            </FilterItem>

                            {(params.strategy === "Resonance" || params.strategy === "Combined") && (
                                <>
                                    <FilterItem label="粘合回溯天数 (1~30)">
                                        <input
                                            type="number" value={isNaN(params.sqz_lookback) ? "" : params.sqz_lookback}
                                            onChange={e => setParams({ ...params, sqz_lookback: parseInt(e.target.value) || 0 })}
                                            className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                        />
                                    </FilterItem>

                                    <ToggleItem
                                        label="极致波动率收缩 (BB)"
                                        active={params.use_bb_sqz}
                                        onClick={() => setParams({ ...params, use_bb_sqz: !params.use_bb_sqz })}
                                    />
                                </>
                            )}

                            <FilterItem label="市场范围限制">
                                <select
                                    value={params.market_range}
                                    onChange={e => setParams({ ...params, market_range: e.target.value })}
                                    className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                >
                                    <option value="全市场(除科创)">全市场(除科创)</option>
                                    <option value="包含科创板">全市场(包含科创)</option>
                                    <option value="沪深300">沪深300</option>
                                    <option value="上证50">上证50</option>
                                    <option value="中证500">中证500</option>
                                    <option value="中证1000">中证1000</option>
                                </select>
                            </FilterItem>

                            <div className="pt-4 border-t border-slate-100">
                                <label className="flex items-center gap-3 px-4 py-3 rounded-xl border-2 border-indigo-100 bg-indigo-50/20 cursor-pointer transition-all hover:bg-indigo-50/40">
                                    <div className="flex-1">
                                        <span className="block font-bold text-indigo-700">🚀 本地极速扫描模式</span>
                                        <span className="text-[10px] text-indigo-400 font-medium">仅使用本地数据库，无需网络，秒级出结果</span>
                                    </div>
                                    <input
                                        type="checkbox"
                                        checked={params.local_only}
                                        onChange={e => setParams({ ...params, local_only: e.target.checked })}
                                        className="w-5 h-5 rounded border-indigo-300 text-indigo-600 focus:ring-indigo-500"
                                    />
                                </label>
                            </div>

                            <div className="pt-2">
                                <label className="flex items-center gap-3 px-4 py-3 rounded-xl border-2 border-rose-100 bg-rose-50/20 cursor-pointer transition-all hover:bg-rose-50/40">
                                    <div className="flex-1">
                                        <span className="block font-bold text-rose-700">🎯 仅显示新信号 (买点)</span>
                                        <span className="text-[10px] text-rose-400 font-medium">排除已突破多日的个股，仅看首日触发</span>
                                    </div>
                                    <input
                                        type="checkbox"
                                        checked={params.only_signals}
                                        onChange={e => setParams({ ...params, only_signals: e.target.checked })}
                                        className="w-5 h-5 rounded border-rose-300 text-rose-600 focus:ring-rose-500"
                                    />
                                </label>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="px-8 py-6 bg-slate-50 flex justify-end gap-3">
                    <button onClick={onClose} className="px-6 py-2.5 rounded-xl font-bold text-slate-500 hover:bg-slate-200 transition-all">取消</button>
                    <button
                        onClick={() => { onScan(); onClose(); }}
                        className="px-8 py-2.5 premium-gradient text-white rounded-xl font-bold shadow-lg shadow-indigo-100 transition-all hover:scale-105 active:scale-95"
                    >
                        保存并执行
                    </button>
                </div>
            </div>
        </div>
    );
}

function FilterItem({ label, children }: { label: string, children: React.ReactNode }) {
    return (
        <div className="space-y-2">
            <label className="text-xs font-extrabold text-slate-500 uppercase tracking-wider">{label}</label>
            {children}
        </div>
    );
}

function ToggleItem({ label, active, onClick }: { label: string, active: boolean, onClick: () => void }) {
    return (
        <div
            onClick={onClick}
            className={cn(
                "flex items-center justify-between p-3 rounded-xl border-2 cursor-pointer transition-all",
                active ? "border-indigo-600 bg-indigo-50/50" : "border-slate-100 bg-slate-50/30 hover:border-slate-200"
            )}
        >
            <span className={cn("text-xs font-bold", active ? "text-indigo-600" : "text-slate-400")}>{label}</span>
            <div className={cn(
                "w-5 h-5 rounded-md flex items-center justify-center transition-all",
                active ? "bg-indigo-600 text-white" : "bg-slate-200 text-transparent"
            )}>
                <Check size={14} strokeWidth={3} />
            </div>
        </div>
    );
}
