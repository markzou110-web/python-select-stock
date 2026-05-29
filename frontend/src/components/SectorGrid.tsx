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
        <div className="space-y-3">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-black text-slate-900 uppercase tracking-widest flex items-center gap-2">
                    <Flame size={18} className="text-rose-600" />
                    今日领涨板块
                </h3>
                <button className="text-xs font-black text-blue-700 hover:underline">查看全部</button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
                {sectors.length > 0 ? sectors.map((sector, i) => (
                    <div key={i} className="glass-card p-4 group flex flex-col justify-between h-28 relative overflow-hidden transition-colors duration-150 hover:border-blue-200">
                        <div>
                            <p className="metric-label mb-1">{sector.name}</p>
                            <div className="flex items-baseline gap-2">
                                <span className="text-xl font-black font-mono text-rose-600">+{sector.pct.toFixed(2)}%</span>
                            </div>
                        </div>

                        <div className="flex items-center gap-1.5 mt-2">
                            <TrendingUp size={12} className="text-slate-300" />
                            <span className="text-[10px] font-bold text-slate-400">领涨: <span className="text-slate-600">{sector.lead}</span></span>
                        </div>
                    </div>
                )) : (
                    Array(5).fill(0).map((_, i) => (
                        <div key={i} className="glass-card p-5 h-28 animate-pulse bg-slate-50" />
                    ))
                )}
            </div>
        </div>
    );
}
