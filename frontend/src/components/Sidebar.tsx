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
    Square,
    Star,
    ClipboardList,
    SlidersHorizontal
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAlertStore } from '@/stores/alertStore';

interface SidebarProps {
    syncProgress: any;
    onStartSync: () => void;
    onStartSyncFundamentals?: () => void;
    onStopSync: () => void;
    activeView: string;
    onNavigate: (view: string) => void;
}

export default function Sidebar({ syncProgress, onStartSync, onStartSyncFundamentals, onStopSync, activeView, onNavigate }: SidebarProps) {
    const isRunning = syncProgress?.is_running;
    const progress = syncProgress?.total > 0 ? (syncProgress.current / syncProgress.total) * 100 : 0;
    const unreadCount = useAlertStore(s => s.unreadCount);

    return (
        <div className="w-[252px] h-full bg-white border-r border-slate-200 flex flex-col z-20">
            {/* Header */}
            <div className="px-5 py-4 flex-shrink-0 border-b border-slate-200">
                <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-slate-900 flex items-center justify-center text-white">
                        <Cpu size={20} />
                    </div>
                    <div className="min-w-0">
                        <h1 className="font-black text-base leading-tight text-slate-950">Alpha Vision</h1>
                        <p className="text-[10px] text-slate-500 font-bold tracking-widest uppercase">Quant Research</p>
                    </div>
                </div>
            </div>

            {/* Scrollable Navigation */}
            <div className="flex-1 overflow-y-auto px-3 py-4">
                <div className="px-2 pb-2 metric-label">工作台</div>
                <nav className="space-y-0.5">
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
                        icon={<Star size={20} />}
                        label="观察池"
                        active={activeView === 'watchlist'}
                        onClick={() => onNavigate('watchlist')}
                    />
                    <NavItem
                        icon={<ClipboardList size={20} />}
                        label="交易复盘"
                        active={activeView === 'review'}
                        onClick={() => onNavigate('review')}
                    />
                    <NavItem
                        icon={<Bell size={20} />}
                        label="实时告警"
                        active={activeView === 'alerts'}
                        onClick={() => onNavigate('alerts')}
                        badge={unreadCount}
                    />
                </nav>
            </div>

            {/* Fixed Bottom Section */}
            <div className="flex-shrink-0 p-3 border-t border-slate-200 bg-slate-50">
                <div className="mb-4">
                    <h3 className="metric-label mb-3 px-1">数据管理</h3>

                    <div className="space-y-2">
                        <button
                            onClick={onStartSync}
                            disabled={isRunning}
                            className={cn(
                                "w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md border text-xs font-bold transition-colors",
                                isRunning
                                    ? "bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed"
                                    : "bg-white border-slate-200 text-slate-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
                            )}
                        >
                            <RefreshCw size={16} className={cn(isRunning && "animate-spin")} />
                            {isRunning ? "正在同步..." : "同步行情快照"}
                        </button>
                        
                        <button
                            onClick={onStartSyncFundamentals}
                            disabled={isRunning}
                            className={cn(
                                "w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md border text-xs font-bold transition-colors",
                                isRunning
                                    ? "bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed"
                                    : "bg-white border-slate-200 text-slate-700 hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700"
                            )}
                        >
                            <Database size={16} className={cn(isRunning && "animate-pulse")} />
                            同步最新基本面
                        </button>
                    </div>

                    {isRunning && (
                        <div className="mt-3 p-3 bg-white rounded-lg border border-slate-200 shadow-sm">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-[10px] font-bold text-slate-500 flex items-center gap-1">
                                    <Database size={10} /> {syncProgress?.current}/{syncProgress?.total}
                                </span>
                                <span className="text-[10px] font-black text-blue-700">{Math.round(progress)}%</span>
                            </div>
                            <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-blue-700 transition-all duration-300 rounded-full"
                                    style={{ width: `${progress}%` }}
                                />
                            </div>
                            <p className="mt-2 text-[9px] text-slate-400 leading-tight">
                                {syncProgress?.status_text || "正在同步数据，请稍候..."}
                            </p>
                            
                            <button
                                onClick={onStopSync}
                                className="mt-3 w-full flex items-center justify-center gap-1.5 py-1.5 bg-rose-50 text-rose-700 rounded-md text-[10px] font-bold border border-rose-100 hover:bg-rose-100 transition-colors"
                            >
                                <Square size={10} fill="currentColor" />
                                立即停止同步
                            </button>
                        </div>
                    )}
                </div>

                <div className="space-y-0.5 pt-2 border-t border-slate-200">
                    <NavItem icon={<Search size={20} />} label="代码检索" active={activeView === 'search'} onClick={() => onNavigate('search')} />
                    <NavItem icon={<SlidersHorizontal size={20} />} label="策略模板" active={activeView === 'templates'} onClick={() => onNavigate('templates')} />
                    <NavItem icon={<Settings size={20} />} label="系统配置" active={activeView === 'settings'} onClick={() => onNavigate('settings')} />
                </div>

                <div className="mt-4 rounded-lg border border-slate-200 bg-white px-3 py-2.5">
                    <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">当前版本</p>
                    <p className="mt-1 text-xs font-black text-slate-800">v6.5.0 Alpha Vision Pro</p>
                </div>
            </div>
        </div>
    );
}

function NavItem({ icon, label, active = false, badge = 0, onClick }: { icon: React.ReactNode, label: string, active?: boolean, badge?: number, onClick?: () => void }) {
    return (
        <div
            onClick={onClick}
            className={cn(
                "nav-item cursor-pointer flex items-center justify-between text-slate-600",
                active && "active hover:bg-blue-50 hover:text-blue-700"
            )}
        >
            <div className="flex items-center gap-3">
                {icon}
                <span className="font-bold text-sm">{label}</span>
            </div>
            {badge > 0 && (
                <span className={cn(
                    "px-2 py-0.5 text-[10px] font-black rounded-md ml-auto",
                    active ? "bg-blue-100 text-blue-700" : "bg-rose-600 text-white shadow-sm"
                )}>
                    {badge}
                </span>
            )}
        </div>
    );
}
