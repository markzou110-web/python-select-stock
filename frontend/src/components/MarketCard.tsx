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
            <div className="glass-card p-3 animate-pulse">
                <div className="h-3 w-12 bg-slate-200 rounded mb-3" />
                <div className="h-6 w-24 bg-slate-200 rounded mb-2" />
                <div className="h-4 w-16 bg-slate-200 rounded" />
            </div>
        );
    }

    return (
        <div className="glass-card p-3 group transition-colors duration-150 hover:border-blue-200">
            <div className="flex justify-between items-start mb-2">
                <span className="metric-label">{name}</span>
                <div className={cn(
                    "p-1 rounded-md transition-colors",
                    isUp ? "bg-rose-50 text-rose-600 group-hover:bg-rose-100" : "bg-teal-50 text-teal-600 group-hover:bg-teal-100"
                )}>
                    {isUp ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                </div>
            </div>

            <div className="space-y-1">
                <div className="metric-value text-xl">
                    {price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div className={cn(
                    "text-xs font-black flex items-center gap-1",
                    isUp ? "text-rose-600" : "text-teal-600"
                )}>
                    {isUp ? '+' : ''}{pct.toFixed(2)}%
                    <span className="text-[10px] text-slate-400 font-bold ml-1">今日涨跌幅</span>
                </div>
            </div>
        </div>
    );
}
