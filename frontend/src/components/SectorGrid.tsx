"use client";

import React from 'react';
import { Flame, TrendingUp } from 'lucide-react';

interface Sector {
    name: string;
    pct: number;
    lead: string;
}

export default function SectorGrid({ sectors }: { sectors: Sector[] }) {
    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold flex items-center gap-2">
                    <Flame size={20} className="text-rose-500" />
                    今日领涨板块
                </h3>
                <button className="text-sm font-bold text-indigo-600 hover:underline">查看全部</button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">
                {sectors.length > 0 ? sectors.map((sector, i) => (
                    <div key={i} className="glass-card p-5 group flex flex-col justify-between h-32 relative overflow-hidden transition-all duration-300 hover:scale-[1.02]">
                        <div>
                            <p className="text-xs font-bold text-slate-400 mb-1">{sector.name}</p>
                            <div className="flex items-baseline gap-2">
                                <span className="text-xl font-extrabold text-emerald-500">+{sector.pct.toFixed(2)}%</span>
                            </div>
                        </div>

                        <div className="flex items-center gap-1.5 mt-2">
                            <TrendingUp size={12} className="text-slate-300" />
                            <span className="text-[10px] font-bold text-slate-400">领涨: <span className="text-slate-600">{sector.lead}</span></span>
                        </div>

                        <div className="absolute -right-4 -bottom-4 w-20 h-20 bg-emerald-50 rounded-full opacity-20 group-hover:scale-150 transition-transform duration-700" />
                    </div>
                )) : (
                    Array(5).fill(0).map((_, i) => (
                        <div key={i} className="glass-card p-5 h-32 animate-pulse bg-slate-50" />
                    ))
                )}
            </div>
        </div>
    );
}
