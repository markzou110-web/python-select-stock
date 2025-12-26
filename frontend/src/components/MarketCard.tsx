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
            <div className="glass-card p-6 min-w-[200px] animate-pulse">
                <div className="h-4 w-12 bg-slate-200 rounded mb-4" />
                <div className="h-8 w-24 bg-slate-200 rounded mb-2" />
                <div className="h-4 w-16 bg-slate-200 rounded" />
            </div>
        );
    }

    return (
        <div className="glass-card p-6 min-w-[200px] group transition-all duration-300 hover:scale-[1.02]">
            <div className="flex justify-between items-start mb-4">
                <span className="text-sm font-bold text-slate-400 uppercase tracking-tight">{name}</span>
                <div className={cn(
                    "p-2 rounded-lg transition-colors",
                    isUp ? "bg-emerald-50 text-emerald-600 group-hover:bg-emerald-100" : "bg-rose-50 text-rose-600 group-hover:bg-rose-100"
                )}>
                    {isUp ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
                </div>
            </div>

            <div className="space-y-1">
                <div className="text-3xl font-extrabold tracking-tight text-slate-800">
                    {price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className={cn(
                    "text-sm font-bold flex items-center gap-1",
                    isUp ? "text-emerald-500" : "text-rose-500"
                )}>
                    {isUp ? '+' : ''}{pct.toFixed(2)}%
                    <span className="text-[10px] text-slate-300 font-medium ml-1">今日收益率</span>
                </div>
            </div>
        </div>
    );
}
