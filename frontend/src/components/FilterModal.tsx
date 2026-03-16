"use client";

import React, { useEffect, useState } from 'react';
import { X, Check, Calendar } from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';

interface FilterModalProps {
    isOpen: boolean;
    onClose: () => void;
    params: any;
    setParams: (params: any) => void;
    onScan: () => void;
    availableDates?: Array<{ date: string; stock_count: number }>;
}

export default function FilterModal({ isOpen, onClose, params, setParams, onScan, availableDates = [] }: FilterModalProps) {
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

                <div className="p-8 grid grid-cols-2 gap-8">
                    {/* Column 1 */}
                    <div className="space-y-6">
                        <FilterItem label="粘合度阈值 (0.01~0.30)">
                            <input
                                type="range" min="0.01" max="0.30" step="0.01" value={params.threshold}
                                onChange={e => setParams({ ...params, threshold: parseFloat(e.target.value) })}
                                className="w-full accent-indigo-600"
                            />
                            <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                <span>极限粘合 (0.01)</span>
                                <span className="text-indigo-600 font-extrabold text-xs">{params.threshold.toFixed(2)}</span>
                                <span>宽容粘合 (0.30)</span>
                            </div>
                        </FilterItem>

                        <FilterItem label="量比倍数 (1.0~5.0)">
                            <input
                                type="number" step="0.1" value={params.vol_multiplier}
                                onChange={e => setParams({ ...params, vol_multiplier: parseFloat(e.target.value) })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            />
                        </FilterItem>

                        <div className="space-y-3 pt-2">
                            <ToggleItem
                                label="启用周线趋势过滤"
                                active={params.use_weekly}
                                onClick={() => setParams({ ...params, use_weekly: !params.use_weekly })}
                            />
                            <ToggleItem
                                label="🔥 MACD 必须处于金叉 (红柱)"
                                active={params.use_macd_filter}
                                onClick={() => setParams({ ...params, use_macd_filter: !params.use_macd_filter })}
                            />
                            <FilterItem label="最小换手率 (%)">
                                <input
                                    type="number" step="0.5" value={params.turnover_min}
                                    onChange={e => setParams({ ...params, turnover_min: parseFloat(e.target.value) })}
                                    className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                />
                            </FilterItem>
                            <FilterItem label="最小市值 (亿)">
                                <input
                                    type="number" step="10" value={params.mkt_cap_min}
                                    onChange={e => setParams({ ...params, mkt_cap_min: parseFloat(e.target.value) })}
                                    className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                />
                            </FilterItem>
                            <label className="flex items-center gap-3 px-4 py-3 rounded-xl border-2 border-slate-100 cursor-pointer transition-all hover:bg-slate-50">
                                <span className="flex-1 font-semibold text-slate-600">相对强度过滤 (RS)</span>
                                <input
                                    type="checkbox" checked={params.use_rs_filter}
                                    onChange={e => setParams({ ...params, use_rs_filter: e.target.checked })}
                                    className="w-5 h-5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                                />
                            </label>
                        </div>
                    </div>

                    {/* Column 2 */}
                    <div className="space-y-6">
                        <FilterItem label="RSI 最小强度 (30~80)">
                            <input
                                type="number" value={params.rsi_min}
                                onChange={e => setParams({ ...params, rsi_min: parseInt(e.target.value) })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            />
                        </FilterItem>

                        <FilterItem label="粘合回溯天数 (1~30)">
                            <input
                                type="number" value={params.sqz_lookback}
                                onChange={e => setParams({ ...params, sqz_lookback: parseInt(e.target.value) })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            />
                        </FilterItem>

                        <ToggleItem
                            label="极致波动率收缩 (BB)"
                            active={params.use_bb_sqz}
                            onClick={() => setParams({ ...params, use_bb_sqz: !params.use_bb_sqz })}
                        />

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

                        {/* 数据日期选择器 */}
                        <FilterItem label="📅 选股数据日期">
                            <select
                                value={params.data_date || ""}
                                onChange={e => setParams({ ...params, data_date: e.target.value })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            >
                                <option value="">🔄 自动选择最新日期</option>
                                {availableDates.slice(0, 15).map((d) => (
                                    <option key={d.date} value={d.date}>
                                        📅 {d.date} ({d.stock_count}只股票)
                                    </option>
                                ))}
                            </select>
                            <p className="text-[10px] text-slate-400 mt-1">
                                选择使用哪一天的数据进行选股，留空则自动使用最新可用日期
                            </p>
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
