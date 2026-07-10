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
    SlidersHorizontal,
    ServerCog,
    BarChart3,
    Newspaper
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAlertStore } from '@/stores/alertStore';
import type { SyncProgress } from '@/stores/marketStore';

interface SidebarProps {
    syncProgress: SyncProgress | null;
    onStartSync: () => void;
    onStartSyncFundamentals?: () => void;
    onStopSync: () => void;
    activeView: string;
    onNavigate: (view: string) => void;
}

export default function Sidebar({ syncProgress, onStartSync, onStartSyncFundamentals, onStopSync, activeView, onNavigate }: SidebarProps) {
    const isRunning = syncProgress?.is_running;
    const progress = (syncProgress?.total || 0) > 0 ? ((syncProgress?.current || 0) / (syncProgress?.total || 1)) * 100 : 0;
    const unreadCount = useAlertStore(s => s.unreadCount);

    return (
        <div className="w-[220px] h-full flex flex-col z-20 border-r border-slate-950 bg-[#151b28] text-slate-200">
            {/* Header */}
            <div className="px-4 py-4 flex-shrink-0 border-b border-white/10 bg-[#111827]">
                <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-md premium-gradient flex items-center justify-center text-white">
                        <Cpu size={18} />
                    </div>
                    <div className="min-w-0">
                        <h1 className="font-black text-sm leading-tight text-white">Alpha Vision</h1>
                        <p className="text-[10px] text-amber-200/70 font-semibold tracking-widest uppercase">Quant</p>
                    </div>
                </div>
            </div>

            {/* Scrollable Navigation */}
            <div className="flex-1 overflow-y-auto px-2.5 py-3">
                <div className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500">工作台</div>
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
                        icon={<LayoutDashboard size={20} />}
                        label="板块雷达"
                        active={activeView === 'sector-radar'}
                        onClick={() => onNavigate('sector-radar')}
                    />
                    <NavItem
                        icon={<Newspaper size={20} />}
                        label="资讯雷达"
                        active={activeView === 'research-radar'}
                        onClick={() => onNavigate('research-radar')}
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
                        icon={<BarChart3 size={20} />}
                        label="策略回测"
                        active={activeView === 'backtest'}
                        onClick={() => onNavigate('backtest')}
                    />
                    <NavItem
                        icon={<Bell size={20} />}
                        label="实时告警"
                        active={activeView === 'alerts'}
                        onClick={() => onNavigate('alerts')}
                        badge={unreadCount}
                    />
                    <NavItem
                        icon={<ServerCog size={20} />}
                        label="专业驾驶舱"
                        active={activeView === 'ops'}
                        onClick={() => onNavigate('ops')}
                    />
                </nav>
            </div>

            {/* Fixed Bottom Section */}
            <div className="flex-shrink-0 p-3 border-t border-white/10 bg-[#111827]">
                <div className="mb-3">
                    <h3 className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500">数据管理</h3>

                    <div className="grid gap-2">
                        <button
                            onClick={onStartSync}
                            disabled={isRunning}
                            className={cn(
                                "w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md border text-xs font-semibold transition-colors",
                                isRunning
                                    ? "bg-white/5 border-white/10 text-slate-500 cursor-not-allowed"
                                    : "bg-white/[0.06] border-white/10 text-slate-200 hover:border-amber-300/40 hover:bg-white/10 hover:text-white"
                            )}
                        >
                            <RefreshCw size={16} className={cn(isRunning && "animate-spin")} />
                            {isRunning ? "正在同步..." : "同步行情快照"}
                        </button>
                        
                        <button
                            onClick={onStartSyncFundamentals}
                            disabled={isRunning}
                            className={cn(
                                "w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md border text-xs font-semibold transition-colors",
                                isRunning
                                    ? "bg-white/5 border-white/10 text-slate-500 cursor-not-allowed"
                                    : "bg-white/[0.06] border-white/10 text-slate-200 hover:border-teal-300/40 hover:bg-white/10 hover:text-white"
                            )}
                        >
                            <Database size={16} className={cn(isRunning && "animate-pulse")} />
                            同步最新基本面
                        </button>
                    </div>

                    {isRunning && (
                        <div className="mt-3 p-3 rounded-lg border border-white/10 bg-white/5 shadow-sm">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-[10px] font-bold text-slate-400 flex items-center gap-1">
                                    <Database size={10} /> {syncProgress?.current}/{syncProgress?.total}
                                </span>
                                <span className="text-[10px] font-black text-amber-200">{Math.round(progress)}%</span>
                            </div>
                            <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-amber-300 transition-all duration-300 rounded-full"
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

                <div className="space-y-0.5 pt-2 border-t border-white/10">
                    <NavItem icon={<Search size={20} />} label="代码检索" active={activeView === 'search'} onClick={() => onNavigate('search')} />
                    <NavItem icon={<SlidersHorizontal size={20} />} label="策略模板" active={activeView === 'templates'} onClick={() => onNavigate('templates')} />
                    <NavItem icon={<Settings size={20} />} label="系统配置" active={activeView === 'settings'} onClick={() => onNavigate('settings')} />
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
                "nav-item cursor-pointer flex items-center justify-between text-slate-400 hover:bg-white/[0.07] hover:text-white",
                active && "active text-white hover:bg-white/10"
            )}
        >
            <div className="flex items-center gap-3">
                {icon}
                <span className="font-semibold text-sm">{label}</span>
            </div>
            {badge > 0 && (
                <span className={cn(
                    "px-2 py-0.5 text-[10px] font-black rounded-md ml-auto",
                    active ? "bg-amber-300/20 text-amber-100" : "bg-rose-600 text-white shadow-sm"
                )}>
                    {badge}
                </span>
            )}
        </div>
    );
}
