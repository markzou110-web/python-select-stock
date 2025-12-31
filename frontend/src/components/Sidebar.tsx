"use client";

import React from 'react';
import {
    TrendingUp,
    Search,
    Settings,
    PieChart,
    LayoutDashboard,
    Bell,
    Cpu,
    RefreshCw,
    Database,
    Clock
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface SidebarProps {
    syncProgress: any;
    onStartSync: () => void;
    activeView: string;
    onNavigate: (view: string) => void;
}

export default function Sidebar({ syncProgress, onStartSync, activeView, onNavigate }: SidebarProps) {
    const isRunning = syncProgress?.is_running;
    const progress = syncProgress?.total > 0 ? (syncProgress.current / syncProgress.total) * 100 : 0;
    const [alertMsg, setAlertMsg] = React.useState<string | null>(null);

    const handleAlertClick = () => {
        setAlertMsg("🔔 实时告警服务已激活 (后台运行中)");
        setTimeout(() => setAlertMsg(null), 3000);
    };

    return (
        <div className="w-64 h-full bg-slate-50 border-r border-slate-200 flex flex-col z-20">
            {/* Header */}
            <div className="p-4 flex-shrink-0">
                <div className="flex items-center gap-3 px-2 mb-6">
                    <div className="w-10 h-10 premium-gradient rounded-xl flex items-center justify-center text-white shadow-lg shadow-indigo-200">
                        <Cpu size={24} />
                    </div>
                    <div>
                        <h1 className="font-bold text-lg leading-tight text-slate-900">Alpha Vision</h1>
                        <p className="text-xs text-slate-400 font-medium tracking-wider uppercase">Quant Engine</p>
                    </div>
                </div>
            </div>

            {/* Scrollable Navigation */}
            <div className="flex-1 overflow-y-auto px-4 pb-4">
                <nav className="space-y-1">
                    <NavItem
                        icon={<LayoutDashboard size={20} />}
                        label="系统概览"
                        active={activeView === 'overview'}
                        onClick={() => onNavigate('overview')}
                    />
                    <NavItem
                        icon={<TrendingUp size={20} />}
                        label="多因子共振"
                        active={activeView === 'scanner'}
                        onClick={() => onNavigate('scanner')}
                    />
                    <NavItem
                        icon={<PieChart size={20} />}
                        label="拟合实盘"
                        active={activeView === 'paper'}
                        onClick={() => onNavigate('paper')}
                    />
                    <NavItem
                        icon={<Bell size={20} />}
                        label="实时告警"
                        onClick={handleAlertClick}
                    />
                </nav>
            </div>

            {/* Alert Toast */}
            {alertMsg && (
                <div className="px-4 mb-2 animate-in slide-in-from-bottom-2 fade-in duration-300">
                    <div className="bg-indigo-600 text-white text-xs font-bold px-4 py-3 rounded-xl shadow-lg flex items-center gap-2">
                        <Bell size={14} className="animate-pulse" />
                        {alertMsg}
                    </div>
                </div>
            )}

            {/* Fixed Bottom Section */}
            <div className="flex-shrink-0 p-4 border-t border-slate-100 bg-slate-50">
                <div className="mb-4">
                    <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-4">数据管理</h3>

                    <button
                        onClick={onStartSync}
                        disabled={isRunning}
                        className={cn(
                            "w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border-2 font-bold text-sm transition-all",
                            isRunning
                                ? "bg-slate-100 border-slate-100 text-slate-400 cursor-not-allowed"
                                : "bg-white border-slate-100 text-slate-600 hover:border-indigo-100 hover:bg-indigo-50/50 hover:text-indigo-600"
                        )}
                    >
                        <RefreshCw size={16} className={cn(isRunning && "animate-spin")} />
                        {isRunning ? "正在同步..." : "同步当日数据"}
                    </button>

                    {isRunning && (
                        <div className="mt-4 p-3 bg-white rounded-xl border border-slate-100 shadow-sm">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-[10px] font-bold text-slate-500 flex items-center gap-1">
                                    <Database size={10} /> {syncProgress?.current}/{syncProgress?.total}
                                </span>
                                <span className="text-[10px] font-heavy text-indigo-600">{Math.round(progress)}%</span>
                            </div>
                            <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-indigo-500 transition-all duration-300 rounded-full"
                                    style={{ width: `${progress}%` }}
                                />
                            </div>
                            <p className="mt-2 text-[9px] text-slate-400 leading-tight">
                                {syncProgress?.status_text || "正在同步数据，请稍候..."}
                            </p>
                        </div>
                    )}
                </div>

                <div className="space-y-1 pt-2">
                    <NavItem icon={<Search size={20} />} label="代码检索" />
                    <NavItem icon={<Settings size={20} />} label="系统配置" />
                </div>

                <div className="mt-6 p-4 rounded-2xl bg-indigo-600 text-white shadow-xl shadow-indigo-100 relative overflow-hidden">
                    <div className="relative z-10">
                        <p className="text-xs font-medium opacity-80 mb-1">当前版本</p>
                        <p className="text-sm font-bold">v6.5.0 Alpha Vision Pro</p>
                    </div>
                    <div className="absolute top-0 right-0 w-16 h-16 bg-white/10 rounded-full -translate-y-1/2 translate-x-1/2" />
                </div>
            </div>
        </div>
    );
}

function NavItem({ icon, label, active = false, onClick }: { icon: React.ReactNode, label: string, active?: boolean, onClick?: () => void }) {
    return (
        <div
            onClick={onClick}
            className={cn(
                "nav-item cursor-pointer flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 text-slate-600 hover:bg-slate-100 hover:text-indigo-600",
                active && "bg-indigo-600 text-white shadow-lg shadow-indigo-200 hover:bg-indigo-700 hover:text-white"
            )}
        >
            {icon}
            <span className="font-semibold text-sm">{label}</span>
        </div>
    );
}
