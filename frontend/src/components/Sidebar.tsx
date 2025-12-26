"use client";

import React from 'react';
import {
    TrendingUp,
    Search,
    Settings,
    PieChart,
    LayoutDashboard,
    Bell,
    Cpu
} from 'lucide-react';
import { cn } from '@/lib/utils';

export default function Sidebar() {
    return (
        <div className="w-64 h-full bg-slate-50 border-r border-slate-200 flex flex-col p-4">
            <div className="flex items-center gap-3 px-2 mb-10">
                <div className="w-10 h-10 premium-gradient rounded-xl flex items-center justify-center text-white shadow-lg shadow-indigo-200">
                    <Cpu size={24} />
                </div>
                <div>
                    <h1 className="font-bold text-lg leading-tight">Alpha Vision</h1>
                    <p className="text-xs text-slate-400 font-medium tracking-wider uppercase">Quant Engine</p>
                </div>
            </div>

            <nav className="flex-1 space-y-1">
                <NavItem icon={<LayoutDashboard size={20} />} label="系统概览" active />
                <NavItem icon={<TrendingUp size={20} />} label="多因子共振" />
                <NavItem icon={<PieChart size={20} />} label="行业热点" />
                <NavItem icon={<Bell size={20} />} label="实时告警" />
            </nav>

            <div className="mt-auto space-y-1">
                <NavItem icon={<Search size={20} />} label="代码检索" />
                <NavItem icon={<Settings size={20} />} label="系统配置" />
            </div>

            <div className="mt-8 p-4 rounded-2xl bg-indigo-600 text-white shadow-xl shadow-indigo-100 relative overflow-hidden">
                <div className="relative z-10">
                    <p className="text-xs font-medium opacity-80 mb-1">当前版本</p>
                    <p className="text-sm font-bold">v5.1.0 High-Fidelity</p>
                </div>
                <div className="absolute top-0 right-0 w-16 h-16 bg-white/10 rounded-full -translate-y-1/2 translate-x-1/2" />
            </div>
        </div>
    );
}

function NavItem({ icon, label, active = false }: { icon: React.ReactNode, label: string, active?: boolean }) {
    return (
        <div className={cn(
            "nav-item cursor-pointer",
            active && "active"
        )}>
            {icon}
            <span className="font-semibold text-sm">{label}</span>
        </div>
    );
}
