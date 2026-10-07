"use client";

import React, { useEffect, useRef } from 'react';
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
    Newspaper,
    Inbox,
    Flame,
    Sparkles,
    X
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
    isOpen: boolean;
    onClose: () => void;
}

export default function Sidebar({
    syncProgress,
    onStartSync,
    onStartSyncFundamentals,
    onStopSync,
    activeView,
    onNavigate,
    isOpen,
    onClose,
}: SidebarProps) {
    const isRunning = syncProgress?.is_running;
    const progress = (syncProgress?.total || 0) > 0 ? ((syncProgress?.current || 0) / (syncProgress?.total || 1)) * 100 : 0;
    const unreadCount = useAlertStore(s => s.unreadCount);
    const sidebarRef = useRef<HTMLElement>(null);
    const closeButtonRef = useRef<HTMLButtonElement>(null);
    const onCloseRef = useRef(onClose);
    const navigate = (view: string) => {
        onNavigate(view);
        onClose();
        requestAnimationFrame(() => document.getElementById('main-content')?.focus());
    };

    useEffect(() => {
        onCloseRef.current = onClose;
    }, [onClose]);

    useEffect(() => {
        if (!isOpen || window.matchMedia('(min-width: 768px)').matches) return;

        const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const appContent = document.getElementById('app-content');
        appContent?.setAttribute('inert', '');
        closeButtonRef.current?.focus();

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                onCloseRef.current();
                return;
            }
            if (event.key !== 'Tab' || !sidebarRef.current) return;

            const controls = Array.from(sidebarRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
            if (controls.length === 0) return;
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };

        document.addEventListener('keydown', handleKeyDown);
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            appContent?.removeAttribute('inert');
            previousFocus?.focus();
        };
    }, [isOpen]);

    return (
        <aside
            ref={sidebarRef}
            id="app-navigation"
            aria-label="主导航"
            className={cn(
                "fixed inset-y-0 start-0 z-50 flex h-dvh w-[min(18rem,86vw)] flex-col border-e border-white/10 text-slate-200 shadow-2xl md:static md:z-20 md:h-full md:w-[248px] md:shrink-0 md:translate-x-0 md:shadow-none",
                "bg-[var(--sidebar)] transition-[transform,visibility] duration-200 md:visible md:pointer-events-auto",
                isOpen ? "visible translate-x-0 pointer-events-auto" : "invisible -translate-x-full pointer-events-none"
            )}
        >
            {/* Header */}
            <div className="flex flex-shrink-0 items-center justify-between border-b border-white/10 px-4 py-4">
                <div className="flex items-center gap-2.5">
                    <div className="premium-gradient flex size-9 items-center justify-center rounded-xl text-white shadow-lg shadow-blue-950/30">
                        <Cpu size={18} aria-hidden="true" />
                    </div>
                    <div className="min-w-0">
                        <p className="text-sm font-bold leading-tight tracking-[0.04em] text-white">Alpha Vision</p>
                        <p className="mt-0.5 text-[11px] font-medium text-slate-400">A 股决策终端</p>
                    </div>
                </div>
                <button
                    ref={closeButtonRef}
                    type="button"
                    onClick={onClose}
                    className="inline-flex size-10 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10 hover:text-white md:hidden"
                    aria-label="关闭主导航"
                >
                    <X size={20} aria-hidden="true" />
                </button>
            </div>

            {/* Scrollable Navigation */}
            <div className="flex-1 overflow-y-auto px-3 py-4">
                <p className="px-2 pb-2 text-[11px] font-semibold tracking-[0.16em] text-slate-500">工作台</p>
                <nav className="space-y-1" aria-label="工作区">
                    <NavItem
                        icon={<LayoutDashboard size={20} />}
                        label="系统概览"
                        active={activeView === 'overview'}
                        onClick={() => navigate('overview')}
                    />
                    <NavItem
                        icon={<TrendingUp size={20} />}
                        label="多因子共振"
                        active={activeView === 'scanner'}
                        onClick={() => navigate('scanner')}
                    />
                    <NavItem
                        icon={<LayoutDashboard size={20} />}
                        label="板块雷达"
                        active={activeView === 'sector-radar'}
                        onClick={() => navigate('sector-radar')}
                    />
                    <NavItem
                        icon={<Flame size={20} />}
                        label="热股排行"
                        active={activeView === 'hot-stocks'}
                        onClick={() => navigate('hot-stocks')}
                    />
                    <NavItem
                        icon={<Newspaper size={20} />}
                        label="资讯雷达"
                        active={activeView === 'research-radar'}
                        onClick={() => navigate('research-radar')}
                    />
                    <NavItem
                        icon={<Sparkles size={20} />}
                        label="题材热点"
                        active={activeView === 'themes'}
                        onClick={() => navigate('themes')}
                    />
                    <NavItem
                        icon={<PieChart size={20} />}
                        label="拟合实盘"
                        active={activeView === 'paper'}
                        onClick={() => navigate('paper')}
                    />
                    <NavItem
                        icon={<Star size={20} />}
                        label="观察池"
                        active={activeView === 'watchlist'}
                        onClick={() => navigate('watchlist')}
                    />
                    <NavItem
                        icon={<ClipboardList size={20} />}
                        label="交易复盘"
                        active={activeView === 'review'}
                        onClick={() => navigate('review')}
                    />
                    <NavItem
                        icon={<Inbox size={20} />}
                        label="执行收件箱"
                        active={activeView === 'execution-inbox'}
                        onClick={() => navigate('execution-inbox')}
                    />
                    <NavItem
                        icon={<BarChart3 size={20} />}
                        label="策略回测"
                        active={activeView === 'backtest'}
                        onClick={() => navigate('backtest')}
                    />
                    <NavItem
                        icon={<Bell size={20} />}
                        label="实时告警"
                        active={activeView === 'alerts'}
                        onClick={() => navigate('alerts')}
                        badge={unreadCount}
                    />
                    <NavItem
                        icon={<ServerCog size={20} />}
                        label="专业驾驶舱"
                        active={activeView === 'ops'}
                        onClick={() => navigate('ops')}
                    />
                </nav>
            </div>

            {/* Fixed Bottom Section */}
            <div className="flex-shrink-0 border-t border-white/10 bg-[var(--sidebar-raised)] p-3">
                <div className="mb-3">
                    <h2 className="mb-2 px-1 text-[11px] font-semibold tracking-[0.16em] text-slate-500">数据管理</h2>

                    <div className="grid gap-2">
                        <button
                            type="button"
                            onClick={onStartSync}
                            disabled={isRunning}
                            className={cn(
                                "flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors",
                                isRunning
                                    ? "bg-white/5 border-white/10 text-slate-500 cursor-not-allowed"
                                    : "bg-white/[0.06] border-white/10 text-slate-200 hover:border-blue-300/40 hover:bg-white/10 hover:text-white"
                            )}
                        >
                            <RefreshCw size={16} aria-hidden="true" className={cn(isRunning && "animate-spin")} />
                            {isRunning ? "正在同步…" : "同步行情快照"}
                        </button>
                        
                        <button
                            type="button"
                            onClick={onStartSyncFundamentals}
                            disabled={isRunning}
                            className={cn(
                                "flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors",
                                isRunning
                                    ? "bg-white/5 border-white/10 text-slate-500 cursor-not-allowed"
                                    : "bg-white/[0.06] border-white/10 text-slate-200 hover:border-teal-300/40 hover:bg-white/10 hover:text-white"
                            )}
                        >
                            <Database size={16} aria-hidden="true" className={cn(isRunning && "animate-pulse")} />
                            同步最新基本面
                        </button>
                    </div>

                    {isRunning && (
                        <div className="mt-3 rounded-xl border border-white/10 bg-white/5 p-3" role="status" aria-live="polite">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-[10px] font-bold text-slate-400 flex items-center gap-1">
                                    <Database size={10} aria-hidden="true" /> {syncProgress?.current}/{syncProgress?.total}
                                </span>
                                <span className="text-xs font-bold tabular-nums text-blue-200">{Math.round(progress)}%</span>
                            </div>
                            <div
                                className="h-1.5 w-full overflow-hidden rounded-full bg-white/10"
                                role="progressbar"
                                aria-label="数据同步进度"
                                aria-valuemin={0}
                                aria-valuemax={100}
                                aria-valuenow={Math.round(progress)}
                            >
                                <div
                                    className="h-full rounded-full bg-blue-300 transition-[width] duration-300"
                                    style={{ width: `${progress}%` }}
                                />
                            </div>
                            <p className="mt-2 text-xs leading-snug text-slate-400">
                                {syncProgress?.status_text || "正在同步数据，请稍候…"}
                            </p>
                            
                            <button
                                type="button"
                                onClick={onStopSync}
                                className="mt-3 flex min-h-10 w-full items-center justify-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 py-2 text-xs font-semibold text-rose-700 transition-colors hover:bg-rose-100"
                            >
                                <Square size={11} fill="currentColor" aria-hidden="true" />
                                停止同步
                            </button>
                        </div>
                    )}
                </div>

                <div className="space-y-1 border-t border-white/10 pt-2">
                    <NavItem icon={<Search size={20} />} label="代码检索" active={activeView === 'search'} onClick={() => navigate('search')} />
                    <NavItem icon={<SlidersHorizontal size={20} />} label="策略模板" active={activeView === 'templates'} onClick={() => navigate('templates')} />
                    <NavItem icon={<Settings size={20} />} label="系统配置" active={activeView === 'settings'} onClick={() => navigate('settings')} />
                </div>
            </div>
        </aside>
    );
}

function NavItem({ icon, label, active = false, badge = 0, onClick }: { icon: React.ReactNode, label: string, active?: boolean, badge?: number, onClick?: () => void }) {
    return (
        <button
            type="button"
            onClick={onClick}
            aria-current={active ? 'page' : undefined}
            className={cn(
                "nav-item justify-between text-slate-400 hover:translate-x-0.5 hover:bg-white/[0.07] hover:text-white",
                active && "active text-white hover:bg-white/10"
            )}
        >
            <div className="flex items-center gap-3">
                <span aria-hidden="true">{icon}</span>
                <span className="font-semibold text-sm">{label}</span>
            </div>
            {badge > 0 && (
                <span className={cn(
                    "ms-auto rounded-full px-2 py-0.5 text-[11px] font-bold tabular-nums",
                    active ? "bg-blue-300/20 text-blue-100" : "bg-rose-600 text-white"
                )}>
                    {badge}
                </span>
            )}
        </button>
    );
}
