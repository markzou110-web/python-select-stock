"use client";

import React from 'react';
import { Activity, Bell, Database, PieChart, Search, Target, TrendingUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useMarketStore } from '@/stores/marketStore';
import { useScanStore } from '@/stores/scanStore';
import MarketSentiment from '@/components/MarketSentiment';

interface OverviewViewProps {
    onNavigate: (view: string) => void;
}

export default function OverviewView({ onNavigate }: OverviewViewProps) {
    const indices = useMarketStore(s => s.indices);
    const sectors = useMarketStore(s => s.sectors);
    const syncProgress = useMarketStore(s => s.syncProgress);
    const marketRegime = useMarketStore(s => s.marketRegime);
    const results = useScanStore(s => s.results);
    const historyDates = useScanStore(s => s.historyDates);

    const topSector = sectors?.[0];
    const topResult = results?.[0];

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-2xl font-black text-slate-900">系统概览</h2>
                    <p className="text-xs text-slate-400 font-bold uppercase tracking-widest mt-1">Decision cockpit</p>
                </div>
                <div className={cn(
                    "px-4 py-2 rounded-2xl text-sm font-black border",
                    marketRegime?.status === "OFFENSIVE" && "bg-emerald-50 border-emerald-100 text-emerald-700",
                    marketRegime?.status === "CRITICAL" && "bg-rose-50 border-rose-100 text-rose-700",
                    (!marketRegime || marketRegime.status === "DEFENSIVE") && "bg-amber-50 border-amber-100 text-amber-700"
                )}>
                    {marketRegime?.desc || "等待市场状态"}
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
                <OverviewCard icon={<Target size={20} />} label="最新扫描结果" value={`${results.length}`} sub={topResult ? `${topResult.名称} ${topResult.Score?.toFixed?.(1) || topResult.Score}` : "暂无结果"} color="text-indigo-600 bg-indigo-50" />
                <OverviewCard icon={<Database size={20} />} label="行情同步" value={syncProgress?.is_running ? `${Math.round((syncProgress.current / Math.max(syncProgress.total, 1)) * 100)}%` : "空闲"} sub={syncProgress?.status_text || "本地数据仓库可用"} color="text-slate-600 bg-slate-50" />
                <OverviewCard icon={<TrendingUp size={20} />} label="大盘指数" value={`${Object.keys(indices || {}).length}`} sub={Object.entries(indices || {})[0]?.[0] || "等待加载"} color="text-emerald-600 bg-emerald-50" />
                <OverviewCard icon={<PieChart size={20} />} label="领涨板块" value={topSector?.name || "暂无"} sub={topSector?.pct !== undefined ? `${topSector.pct >= 0 ? '+' : ''}${topSector.pct}%` : `${sectors?.length || 0} 个板块`} color="text-rose-600 bg-rose-50" />
            </div>

            <MarketSentiment />

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                <QuickAction icon={<Search size={20} />} title="从个股开始" body="检索任意股票，直接进入深度分析与观察池。" onClick={() => onNavigate('search')} />
                <QuickAction icon={<Activity size={20} />} title="验证策略表现" body={`已有 ${historyDates.length} 个扫描日期，可进入复盘中心查看信号表现。`} onClick={() => onNavigate('review')} />
                <QuickAction icon={<Bell size={20} />} title="处理风险告警" body="查看持仓止损、趋势破位和移动止盈提醒。" onClick={() => onNavigate('alerts')} />
            </div>
        </div>
    );
}

function OverviewCard({ icon, label, value, sub, color }: { icon: React.ReactNode; label: string; value: string; sub: string; color: string }) {
    return (
        <div className="glass-card p-6 flex items-center gap-5">
            <div className={cn("w-14 h-14 rounded-2xl flex items-center justify-center", color)}>{icon}</div>
            <div className="min-w-0">
                <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{label}</p>
                <h3 className="text-xl font-black text-slate-900 mt-1 truncate">{value}</h3>
                <p className="text-xs font-bold text-slate-400 mt-1 truncate">{sub}</p>
            </div>
        </div>
    );
}

function QuickAction({ icon, title, body, onClick }: { icon: React.ReactNode; title: string; body: string; onClick: () => void }) {
    return (
        <button onClick={onClick} className="glass-card p-6 text-left hover:-translate-y-0.5 hover:shadow-xl transition-all">
            <div className="w-11 h-11 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center mb-4">{icon}</div>
            <h3 className="font-black text-slate-800">{title}</h3>
            <p className="text-sm text-slate-400 font-medium mt-2 leading-relaxed">{body}</p>
        </button>
    );
}
