"use client";

import React from 'react';
import { Bell, Search, User, Play, Loader2, Clock } from 'lucide-react';

interface HeaderProps {
    onScan: () => void;
    loading: boolean;
    lastUpdated: string;
    onOpenFilters: () => void;
}

export default function Header({ onScan, loading, lastUpdated, onOpenFilters }: HeaderProps) {
    return (
        <header className="flex items-center justify-between px-8 py-6">
            <div>
                <h2 className="text-2xl font-extrabold text-slate-800 tracking-tight flex items-center gap-2">
                    Alpha Vision <span className="text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded text-xs font-bold">PRO</span>
                </h2>
                <p className="text-sm text-slate-400 font-medium mt-1 flex items-center gap-2">
                    <Clock size={12} /> 最后同步: {lastUpdated}
                </p>
            </div>

            <div className="flex items-center gap-4">
                <button
                    onClick={onScan}
                    disabled={loading}
                    className="flex items-center gap-2 px-6 py-2.5 premium-gradient text-white rounded-xl font-bold shadow-lg shadow-indigo-100 transition-all hover:scale-105 active:scale-95 disabled:opacity-50"
                >
                    {loading ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                    {loading ? '正在分析...' : '一键扫描'}
                </button>

                <div className="h-8 w-[1px] bg-slate-200 mx-2" />

                <div className="relative group">
                    <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none text-slate-400 group-focus-within:text-indigo-500 transition-colors">
                        <Search size={18} />
                    </div>
                    <input
                        type="text"
                        placeholder="代码/名称搜索..."
                        className="pl-10 pr-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-medium w-48 focus:ring-2 focus:ring-indigo-500 transition-all outline-none"
                    />
                </div>

                <div className="w-10 h-10 bg-slate-100 rounded-full flex items-center justify-center text-slate-500 cursor-pointer hover:bg-indigo-50 hover:text-indigo-600 transition-all">
                    <User size={20} />
                </div>
            </div>
        </header>
    );
}
