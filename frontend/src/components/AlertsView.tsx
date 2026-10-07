"use client";

import React, { useEffect } from 'react';
import { useAlertStore } from '@/stores/alertStore';
import { 
    AlertTriangle, 
    BellRing, 
    XOctagon, 
    TrendingDown, 
    ShieldAlert, 
    RefreshCw,
    CheckCircle2
} from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';

export default function AlertsView() {
    const { alerts, loading, fetchAlerts, dismissAlert, dismissAll } = useAlertStore();

    useEffect(() => {
        fetchAlerts();
        
        // Auto-refresh every 30 seconds
        const intervalId = setInterval(fetchAlerts, 30000);
        return () => clearInterval(intervalId);
    }, [fetchAlerts]);

    const activeAlerts = alerts.filter(a => !a.dismissed);
    const priorityCounts = {
        P0: activeAlerts.filter(a => a.priority === 'P0').length,
        P1: activeAlerts.filter(a => a.priority === 'P1').length,
        P2: activeAlerts.filter(a => a.priority === 'P2').length,
    };

    const handleClosePosition = async (id: number, currentPrice: number) => {
        if (!window.confirm("确定要按当前价平仓吗？平仓后该记录将移至已平仓列表。")) return;
        
        try {
            await api.post(`/api/paper/close/${id}`, {
                close_price: currentPrice
            });
            // 刷新警报和本地状态
            fetchAlerts();
            alert("平仓成功");
        } catch (e) {
            console.error(e);
            alert("平仓失败");
        }
    };

    return (
        <div className="glass-card overflow-hidden border-none shadow-2xl shadow-slate-200/50 flex flex-col h-full">
            <div className="px-8 py-6 border-b border-slate-100 flex items-center justify-between bg-white shrink-0">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-rose-50 text-rose-600 rounded-xl flex items-center justify-center relative">
                        <BellRing size={20} className={activeAlerts.length > 0 ? "animate-pulse" : ""} />
                        {activeAlerts.length > 0 && (
                            <span className="absolute -top-1 -right-1 flex h-3 w-3">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-3 w-3 bg-rose-500 border-2 border-white"></span>
                            </span>
                        )}
                    </div>
                    <div>
                        <h3 className="text-lg font-bold text-slate-800">实时风险告警</h3>
                        <p className="text-xs text-slate-400 font-bold uppercase tracking-widest">
                            Live Risk Monitor
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <button 
                        onClick={() => fetchAlerts()} 
                        disabled={loading}
                        className="flex items-center gap-2 px-4 py-2 bg-slate-50 text-slate-500 rounded-xl text-sm font-bold hover:bg-slate-100 transition-all disabled:opacity-50"
                    >
                        <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
                        {loading ? "更新中..." : "手动刷新"}
                    </button>
                    {activeAlerts.length > 0 && (
                        <button 
                            onClick={dismissAll} 
                            className="flex items-center gap-2 px-4 py-2 bg-indigo-50 text-indigo-600 rounded-xl text-sm font-bold hover:bg-indigo-100 transition-all"
                        >
                            <CheckCircle2 size={16} />
                            全部已读
                        </button>
                    )}
                </div>
            </div>

            <div className="flex-1 overflow-y-auto bg-slate-50 p-6">
                {activeAlerts.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-400 opacity-60">
                        <ShieldAlert size={64} className="mb-4 text-emerald-300" />
                        <p className="font-bold text-lg text-slate-500">当前没有风险告警</p>
                        <p className="text-sm font-medium mt-1">您的持仓都在安全判定范围内</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 gap-4 max-w-5xl mx-auto">
                        <div className="grid grid-cols-3 gap-3">
                            <AlertMetric label="P0 立即处理" value={priorityCounts.P0} hot={priorityCounts.P0 > 0} />
                            <AlertMetric label="P1 盘中决策" value={priorityCounts.P1} hot={priorityCounts.P1 > 0} />
                            <AlertMetric label="P2 观察提醒" value={priorityCounts.P2} />
                        </div>
                        {activeAlerts.map(alert => (
                            <div 
                                key={`${alert.id}-${alert.timestamp}`} 
                                className={cn(
                                    "p-5 bg-white rounded-2xl shadow-sm border-2 animate-in fade-in slide-in-from-bottom-2 duration-300 transition-all hover:shadow-md",
                                    alert.level === 'critical' ? "border-rose-100" : "border-amber-100"
                                )}
                            >
                                <div className="flex justify-between items-start">
                                    <div className="flex items-start gap-4">
                                        <div className={cn(
                                            "w-12 h-12 rounded-xl flex items-center justify-center shrink-0",
                                            alert.level === 'critical' ? "bg-rose-50 text-rose-500" : "bg-amber-50 text-amber-500"
                                        )}>
                                            {alert.level === 'critical' ? <XOctagon size={24} /> : <AlertTriangle size={24} />}
                                        </div>
                                        <div className="flex flex-col gap-1">
                                            <div className="flex items-center gap-2">
                                                <h4 className="text-lg font-bold text-slate-800">{alert.name}</h4>
                                                <span className="text-xs font-mono font-bold text-slate-400">{alert.code}</span>
                                                <span className={cn(
                                                    "px-2 py-0.5 rounded-lg text-[10px] font-black uppercase tracking-wider",
                                                    alert.level === 'critical' ? "bg-rose-500 text-white" : "bg-amber-500 text-white"
                                                )}>
                                                    {alert.level === 'critical' ? '极度危险' : '注意预警'}
                                                </span>
                                                {alert.priority && (
                                                    <span className={cn(
                                                        "px-2 py-0.5 rounded-lg text-[10px] font-black",
                                                        alert.priority === 'P0' ? "bg-rose-100 text-rose-700" :
                                                        alert.priority === 'P1' ? "bg-amber-100 text-amber-700" :
                                                        "bg-slate-100 text-slate-600"
                                                    )}>
                                                        {alert.priority} · {alert.priority_label || '提醒'}
                                                    </span>
                                                )}
                                            </div>
                                            
                                            <div className="mt-2 space-y-1">
                                                {alert.reasons.map((r, i) => (
                                                    <p key={i} className="text-sm text-slate-600 flex items-center gap-2 font-medium">
                                                        <TrendingDown size={14} className={alert.level === 'critical' ? 'text-rose-400' : 'text-amber-400'} />
                                                        {r}
                                                    </p>
                                                ))}
                                            </div>

                                            <p className="text-xs font-bold mt-2 text-indigo-600 bg-indigo-50 inline-block px-3 py-1 rounded-lg">
                                                💡 {alert.suggestion}
                                            </p>
                                            {alert.action_line && (
                                                <p className="text-xs font-black mt-2 text-slate-700 bg-white border border-slate-100 inline-block px-3 py-1 rounded-lg">
                                                    {alert.action_line}
                                                </p>
                                            )}
                                        </div>
                                    </div>

                                    <div className="flex flex-col items-end gap-3 shrink-0">
                                        <div className="text-right bg-slate-50 px-4 py-2 rounded-xl border border-slate-100">
                                            <div className="flex items-center gap-3">
                                                <div className="text-left">
                                                    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">现价</p>
                                                    <p className="font-extrabold text-slate-800">{alert.current_price.toFixed(2)}</p>
                                                </div>
                                                <div className="w-[1px] h-6 bg-slate-200" />
                                                <div className="text-left">
                                                    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">浮动盈亏</p>
                                                    <p className={cn(
                                                        "font-extrabold text-lg",
                                                        alert.pl_pct < 0 ? "text-emerald-500" : "text-rose-500"
                                                    )}>
                                                        {alert.pl_pct}%
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                        
                                        <div className="flex gap-2">
                                            <button 
                                                onClick={() => dismissAlert(alert.id)}
                                                className="px-4 py-2 text-xs font-bold text-slate-500 bg-white border border-slate-200 rounded-xl hover:bg-slate-50 transition-colors"
                                            >
                                                忽略
                                            </button>
                                            <button 
                                                onClick={() => handleClosePosition(alert.id, alert.current_price)}
                                                className={cn(
                                                    "px-4 py-2 text-xs font-bold text-white rounded-xl transition-all shadow-md hover:scale-[1.02] active:scale-[0.98]",
                                                    alert.level === 'critical' ? "bg-rose-500 hover:bg-rose-600 shadow-rose-200" : "bg-amber-500 hover:bg-amber-600 shadow-amber-200"
                                                )}
                                            >
                                                执行平仓
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}

function AlertMetric({ label, value, hot = false }: { label: string; value: number; hot?: boolean }) {
    return (
        <div className={cn("rounded-xl border px-4 py-3 bg-white", hot ? "border-rose-100" : "border-slate-100")}>
            <p className="text-[10px] font-black text-slate-400">{label}</p>
            <p className={cn("mt-1 text-xl font-black", hot ? "text-rose-600" : "text-slate-700")}>{value}</p>
        </div>
    );
}
