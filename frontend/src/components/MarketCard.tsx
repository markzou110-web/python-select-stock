"use client";

import React from 'react';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { cn } from '@/lib/utils';

interface MarketCardProps {
    name: string;
    price: number;
    pct: number;
    loading?: boolean;
}

export default function MarketCard({ name, price, pct, loading = false }: MarketCardProps) {
    const isUp = pct >= 0;

    if (loading) {
        return (
            <div className="glass-card p-4" aria-hidden="true">
                <div className="mb-4 h-3 w-16 animate-pulse rounded bg-slate-200" />
                <div className="mb-2 h-7 w-28 animate-pulse rounded bg-slate-200" />
                <div className="h-4 w-20 animate-pulse rounded bg-slate-100" />
            </div>
        );
    }

    return (
        <article className="glass-card group p-4 transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-[inset_0_1px_0_oklch(1_0_0/0.55),0_2px_4px_oklch(0.2_0.02_255/0.04),0_18px_40px_oklch(0.2_0.03_255/0.09)]">
            <div className="mb-3 flex items-start justify-between">
                <span className="metric-label">{name}</span>
                <div className={cn(
                    "flex size-8 items-center justify-center rounded-lg transition-colors",
                    isUp ? "bg-rose-50 text-rose-700 group-hover:bg-rose-100" : "bg-emerald-50 text-emerald-700 group-hover:bg-emerald-100"
                )} aria-hidden="true">
                    {isUp ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
                </div>
            </div>

            <div className="space-y-1">
                <div className="metric-value text-2xl tracking-tight">
                    {price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className={cn(
                    "flex items-center gap-2 text-sm font-semibold tabular-nums",
                    isUp ? "text-rose-700" : "text-emerald-700"
                )}>
                    {isUp ? '+' : ''}{pct.toFixed(2)}%
                    <span className="text-xs font-normal text-slate-500">今日涨跌幅</span>
                </div>
            </div>
        </article>
    );
}
