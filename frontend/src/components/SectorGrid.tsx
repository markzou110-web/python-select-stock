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
        <section className="space-y-3" aria-labelledby="leading-sectors-heading">
            <div className="flex items-center justify-between">
                <h2 id="leading-sectors-heading" className="flex items-center gap-2 text-base font-bold text-slate-900">
                    <Flame size={17} className="text-rose-700" aria-hidden="true" />
                    今日领涨板块
                </h2>
                <span className="text-xs text-slate-500">按今日涨幅排序</span>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
                {sectors.length > 0 ? sectors.map((sector, i) => (
                    <article key={`${sector.name}-${i}`} className="glass-card group flex min-h-24 flex-col justify-between overflow-hidden p-4 transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-lg">
                        <div>
                            <p className="metric-label mb-1.5">{sector.name}</p>
                            <div className="flex items-baseline gap-2">
                                <span className="font-mono text-xl font-bold tabular-nums text-rose-700">+{sector.pct.toFixed(2)}%</span>
                            </div>
                        </div>

                        <div className="mt-2 flex items-center gap-1.5">
                            <TrendingUp size={13} className="text-slate-400" aria-hidden="true" />
                            <span className="truncate text-xs text-slate-500">领涨 <span className="font-medium text-slate-700">{sector.lead}</span></span>
                        </div>
                    </article>
                )) : (
                    Array(5).fill(0).map((_, i) => (
                        <div key={i} className="glass-card h-24 animate-pulse bg-slate-50 p-4" aria-hidden="true" />
                    ))
                )}
            </div>
        </section>
    );
}
