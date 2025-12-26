"use client";

import React from 'react';
import { Bell, Search, User, Zap } from 'lucide-react';

export default function Header() {
    return (
        <header className="flex items-center justify-between px-8 py-6">
            <div>
                <h2 className="text-2xl font-extrabold text-slate-800 tracking-tight flex items-center gap-2">
                    Alpha Vision <span className="text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded text-xs font-bold">PRO</span>
                </h2>
                <p className="text-sm text-slate-400 font-medium mt-1">
                    多因子共振突破选股系统 · 极客专供
                </p>
            </div>

            <div className="flex items-center gap-4">
                <div className="relative group">
                    <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none text-slate-400 group-focus-within:text-indigo-500 transition-colors">
                        <Search size={18} />
                    </div>
                    <input
                        type="text"
                        placeholder="全市场搜索..."
                        className="pl-10 pr-4 py-2.5 bg-slate-100 border-none rounded-2xl text-sm font-medium w-64 focus:ring-2 focus:ring-indigo-500 transition-all outline-none"
                    />
                </div>

                <button className="p-2.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all relative">
                    <Bell size={20} />
                    <span className="absolute top-2 right-2 w-2.5 h-2.5 bg-rose-500 border-2 border-white rounded-full" />
                </button>

                <div className="w-10 h-10 bg-slate-200 rounded-full flex items-center justify-center text-slate-500 cursor-pointer overflow-hidden border-2 border-transparent hover:border-indigo-500 transition-all shadow-inner">
                    <User size={20} />
                </div>
            </div>
        </header>
    );
}
