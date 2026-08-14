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
        <div className="space-y-6">
            <section className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between" aria-labelledby="overview-heading">
                <div>
                    <p className="text-xs font-semibold tracking-wide text-blue-700">今日决策驾驶舱</p>
                    <h2 id="overview-heading" className="page-heading mt-1">先看风险，再看机会</h2>
                    <p className="page-description">汇总市场环境、扫描进度与执行入口，帮助你快速确定今天的操作顺序。</p>
                </div>
                <div className={cn(
                    "inline-flex w-fit items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold",
                    marketRegime?.status === "OFFENSIVE" && "bg-emerald-50 border-emerald-200 text-emerald-800",
                    marketRegime?.status === "CRITICAL" && "bg-rose-50 border-rose-200 text-rose-800",
                    (!marketRegime || marketRegime.status === "DEFENSIVE") && "bg-amber-50 border-amber-200 text-amber-800"
                )}>
                    <span className="size-2 rounded-full bg-current" aria-hidden="true" />
                    {marketRegime?.desc || "等待市场状态"}
                </div>
            </section>

            <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 2xl:grid-cols-4" aria-label="关键状态">
                <OverviewCard
                    icon={<Target size={20} />}
                    label="最新扫描结果"
                    value={`${results.length}`}
                    sub={topResult ? `${topResult.名称} · 评分 ${topResult.Score?.toFixed?.(1) || topResult.Score}` : "运行扫描后查看候选股"}
                    color="text-blue-700 bg-blue-50"
                />
                <OverviewCard
                    icon={<Database size={20} />}
                    label="行情同步"
                    value={syncProgress?.is_running ? `${Math.round((syncProgress.current / Math.max(syncProgress.total, 1)) * 100)}%` : "空闲"}
                    sub={syncProgress?.status_text || "本地数据仓库可用"}
                    color="text-slate-700 bg-slate-100"
                />
                <OverviewCard
                    icon={<TrendingUp size={20} />}
                    label="已加载指数"
                    value={`${Object.keys(indices || {}).length}`}
                    sub={Object.entries(indices || {})[0]?.[0] || "等待加载"}
                    color="text-teal-700 bg-teal-50"
                />
                <OverviewCard
                    icon={<PieChart size={20} />}
                    label="领涨板块"
                    value={topSector?.name || "暂无"}
                    sub={topSector?.pct !== undefined ? `${topSector.pct >= 0 ? '+' : ''}${topSector.pct.toFixed(2)}%` : `${sectors?.length || 0} 个板块`}
                    color="text-rose-700 bg-rose-50"
                />
            </section>

            <MarketSentiment />

            <section aria-labelledby="next-actions-heading">
                <div className="mb-3">
                    <h2 id="next-actions-heading" className="text-lg font-bold text-slate-900">建议下一步</h2>
                    <p className="mt-1 text-sm text-slate-500">根据当前工作流，选择一个入口继续。</p>
                </div>
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
                    <QuickAction icon={<Search size={20} />} title="从个股开始" body="检索任意股票，直接进入深度分析与观察池。" onClick={() => onNavigate('search')} />
                    <QuickAction icon={<Activity size={20} />} title="验证策略表现" body={`已有 ${historyDates.length} 个扫描日期，可进入复盘中心查看信号表现。`} onClick={() => onNavigate('review')} />
                    <QuickAction icon={<Bell size={20} />} title="处理风险告警" body="查看持仓止损、趋势破位和移动止盈提醒。" onClick={() => onNavigate('alerts')} />
                </div>
            </section>
        </div>
    );
}

function OverviewCard({ icon, label, value, sub, color }: { icon: React.ReactNode; label: string; value: string; sub: string; color: string }) {
    return (
        <article className="glass-card flex items-center gap-4 p-4 sm:p-5">
            <div className={cn("flex size-11 shrink-0 items-center justify-center rounded-xl", color)} aria-hidden="true">{icon}</div>
            <div className="min-w-0">
                <p className="text-xs font-semibold text-slate-500">{label}</p>
                <p className="mt-0.5 truncate text-xl font-bold tabular-nums text-slate-950">{value}</p>
                <p className="mt-0.5 truncate text-xs text-slate-500" title={sub}>{sub}</p>
            </div>
        </article>
    );
}

function QuickAction({ icon, title, body, onClick }: { icon: React.ReactNode; title: string; body: string; onClick: () => void }) {
    return (
        <button type="button" onClick={onClick} className="glass-card group p-5 text-start transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-lg active:scale-[0.96]">
            <div className="mb-4 flex size-11 items-center justify-center rounded-xl bg-blue-50 text-blue-700 transition-colors group-hover:bg-blue-100" aria-hidden="true">{icon}</div>
            <h3 className="font-semibold text-slate-900">{title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-500">{body}</p>
        </button>
    );
}
