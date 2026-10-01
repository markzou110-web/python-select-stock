"use client";

import React, { useEffect } from 'react';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import MarketCard from '@/components/MarketCard';
import MarketSentiment from '@/components/MarketSentiment';
import SectorGrid from '@/components/SectorGrid';
import FilterModal from '@/components/FilterModal';
import ResultsTable from '@/components/ResultsTable';
import ScanHistoryView from '@/components/ScanHistoryView';
import AIDeepDive from '@/components/AIDeepDive';
import PaperTradingView from '@/components/PaperTradingView';
import SettingsView from '@/components/SettingsView';
import ErrorBoundary from '@/components/ErrorBoundary';
import AlertsView from '@/components/AlertsView';
import OverviewView from '@/components/OverviewView';
import SearchView from '@/components/SearchView';
import ReviewCenter from '@/components/ReviewCenter';
import WatchlistView from '@/components/WatchlistView';
import StrategyTemplatesView from '@/components/StrategyTemplatesView';
import OperationsCenter from '@/components/OperationsCenter';
import BacktestLab from '@/components/BacktestLab';
import StockDetailPage from '@/components/StockDetailPage';
import SectorRadarView from '@/components/SectorRadarView';
import ResearchRadarView from '@/components/ResearchRadarView';
import HotStocksView from '@/components/HotStocksView';
import ThemeHeatView from '@/components/ThemeHeatView';
import ExecutionInbox from '@/components/ExecutionInbox';
import { Download, LayoutGrid, List, Search, Zap, Calendar, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useScanStore } from '@/stores/scanStore';
import { useMarketStore } from '@/stores/marketStore';

interface MarketPulseIndex {
    trend?: string;
    trend_label?: string;
    ema20_gap_pct?: number;
    chg_pct?: number;
}

const signedPct = (value?: number) => value == null || !Number.isFinite(value)
    ? '--'
    : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;

const formatTimestamp = (date: Date) => `${date.toISOString().split('T')[0]} ${date.toLocaleTimeString()}`;

export default function Dashboard() {
    // ── Market store ──
    const indices = useMarketStore(s => s.indices);
    const sectors = useMarketStore(s => s.sectors);
    const syncProgress = useMarketStore(s => s.syncProgress);
    const marketLoading = useMarketStore(s => s.loading);
    const lastUpdated = useMarketStore(s => s.lastUpdated);
    const fetchMarketData = useMarketStore(s => s.fetchMarketData);
    const fetchSyncStatus = useMarketStore(s => s.fetchSyncStatus);
    const fetchMarketPulse = useMarketStore(s => s.fetchMarketPulse);
    const startSync = useMarketStore(s => s.startSync);
    const startSyncFundamentals = useMarketStore(s => s.startSyncFundamentals);
    const stopSync = useMarketStore(s => s.stopSync);
    const setLastUpdated = useMarketStore(s => s.setLastUpdated);
    const marketRegime = useMarketStore(s => s.marketRegime);
    const marketFetchFailed = useMarketStore(s => s.lastFetchFailed);

    // ── Scan store ──
    const results = useScanStore(s => s.results);
    const resultGroups = useScanStore(s => s.resultGroups);
    const activeResultGroup = useScanStore(s => s.activeResultGroup);
    const resultStrategyType = useScanStore(s => s.resultStrategyType);
    const isScanning = useScanStore(s => s.isScanning);
    const scanProgress = useScanStore(s => s.scanProgress);
    const lastScanSummary = useScanStore(s => s.lastScanSummary);
    const selectedStock = useScanStore(s => s.selectedStock);
    const params = useScanStore(s => s.params);
    const historyDates = useScanStore(s => s.historyDates);
    const selectedDate = useScanStore(s => s.selectedDate);
    const availableDates = useScanStore(s => s.availableDates);
    const viewMode = useScanStore(s => s.viewMode);
    const isFilterOpen = useScanStore(s => s.isFilterOpen);
    const setSelectedStock = useScanStore(s => s.setSelectedStock);
    const setActiveResultGroup = useScanStore(s => s.setActiveResultGroup);
    const setParams = useScanStore(s => s.setParams);
    const setViewMode = useScanStore(s => s.setViewMode);
    const setIsFilterOpen = useScanStore(s => s.setIsFilterOpen);
    const startScan = useScanStore(s => s.startScan);
    const loadHistory = useScanStore(s => s.loadHistory);
    const fetchHistory = useScanStore(s => s.fetchHistory);
    const fetchAvailableDates = useScanStore(s => s.fetchAvailableDates);
    const handleExport = useScanStore(s => s.handleExport);

    // ── Local UI state ──
    const [activeView, setActiveView] = React.useState('scanner');
    const [searchDetailStock, setSearchDetailStock] = React.useState<{ code: string; name: string } | null>(null);
    const [isNavigationOpen, setIsNavigationOpen] = React.useState(false);

    // ── Initialization ──
    useEffect(() => {
        const init = async () => {
            useMarketStore.setState({ loading: true });
            await Promise.all([
                fetchMarketData(),
                fetchSyncStatus(),
                fetchHistory(),
                fetchAvailableDates(),
                fetchMarketPulse()
            ]);
            useMarketStore.setState({ loading: false });
            // 只有行情请求全部成功时才刷新时间戳，失败场景由错误横幅提示
            if (!useMarketStore.getState().lastFetchFailed) {
                setLastUpdated(formatTimestamp(new Date()));
            }
        };
        init();

        const intervalId = setInterval(fetchSyncStatus, 5000);
        return () => clearInterval(intervalId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleRetryMarketFetch = async () => {
        await Promise.all([fetchMarketData(), fetchSyncStatus(), fetchMarketPulse()]);
        if (!useMarketStore.getState().lastFetchFailed) {
            setLastUpdated(formatTimestamp(new Date()));
        }
    };

    return (
        <div className="flex min-h-dvh w-full overflow-hidden bg-[var(--background)] md:h-dvh">
            <a href="#main-content" className="skip-link">跳到主要内容</a>
            {isNavigationOpen && (
                <button
                    type="button"
                    className="fixed inset-0 z-40 bg-slate-950/55 backdrop-blur-[2px] md:hidden"
                    onClick={() => setIsNavigationOpen(false)}
                    aria-label="关闭主导航"
                />
            )}
            <Sidebar
                syncProgress={syncProgress}
                onStartSync={startSync}
                onStartSyncFundamentals={startSyncFundamentals}
                onStopSync={stopSync}
                activeView={activeView}
                onNavigate={(view) => {
                    setSearchDetailStock(null);
                    setActiveView(view);
                }}
                isOpen={isNavigationOpen}
                onClose={() => setIsNavigationOpen(false)}
            />
            <div id="app-content" className="app-canvas flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                <Header
                    onScan={startScan}
                    loading={isScanning}
                    lastUpdated={lastUpdated}
                    onOpenFilters={() => setIsFilterOpen(true)}
                    onSelectStock={(stock) => {
                        setSelectedStock(null);
                        setSearchDetailStock({
                            code: stock.code,
                            name: stock.name,
                        });
                    }}
                    activeView={activeView}
                    onOpenNavigation={() => setIsNavigationOpen(true)}
                />

                <main id="main-content" tabIndex={-1} className="flex-1 overflow-y-auto px-3 py-4 sm:px-5 sm:py-5">
                    {marketFetchFailed && (
                        <div
                            className="mx-auto mb-4 flex w-full max-w-[1920px] items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3"
                            role="alert"
                        >
                            <div className="flex items-center gap-2 text-sm font-bold text-rose-800">
                                <AlertTriangle size={16} aria-hidden="true" />
                                <span>行情服务未响应，数据可能过期</span>
                            </div>
                            <button
                                type="button"
                                onClick={handleRetryMarketFetch}
                                className="rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-black text-rose-700 hover:bg-rose-100"
                            >
                                重试
                            </button>
                        </div>
                    )}
                    <div className="mx-auto flex w-full max-w-[1920px] flex-col items-start gap-5 xl:flex-row">
                        <div className={cn("min-w-0 space-y-5 transition-[width] duration-300", selectedStock ? "flex-1" : "w-full")}>
                            {searchDetailStock ? (
                                <ErrorBoundary fallbackTitle="个股详情加载异常">
                                    <StockDetailPage
                                        code={searchDetailStock.code}
                                        name={searchDetailStock.name}
                                        onBack={() => setSearchDetailStock(null)}
                                    />
                                </ErrorBoundary>
                            ) : activeView === 'overview' ? (
                                <ErrorBoundary fallbackTitle="系统概览加载异常">
                                    <OverviewView onNavigate={setActiveView} />
                                </ErrorBoundary>
                            ) : activeView === 'search' ? (
                                <ErrorBoundary fallbackTitle="代码检索加载异常">
                                    <SearchView />
                                </ErrorBoundary>
                            ) : activeView === 'review' ? (
                                <ErrorBoundary fallbackTitle="交易复盘加载异常">
                                    <ReviewCenter />
                                </ErrorBoundary>
                            ) : activeView === 'execution-inbox' ? (
                                <ErrorBoundary fallbackTitle="执行收件箱加载异常">
                                    <ExecutionInbox />
                                </ErrorBoundary>
                            ) : activeView === 'backtest' ? (
                                <ErrorBoundary fallbackTitle="策略回测加载异常">
                                    <BacktestLab />
                                </ErrorBoundary>
                            ) : activeView === 'watchlist' ? (
                                <ErrorBoundary fallbackTitle="观察池加载异常">
                                    <WatchlistView
                                        onOpenStock={(stock) => {
                                            setSelectedStock(null);
                                            setSearchDetailStock(stock);
                                        }}
                                    />
                                </ErrorBoundary>
                            ) : activeView === 'sector-radar' ? (
                                <ErrorBoundary fallbackTitle="板块雷达加载异常">
                                    <SectorRadarView />
                                </ErrorBoundary>
                            ) : activeView === 'hot-stocks' ? (
                                <ErrorBoundary fallbackTitle="热股排行加载异常">
                                    <HotStocksView
                                        onOpenStock={(stock) => {
                                            setSelectedStock(null);
                                            setSearchDetailStock(stock);
                                        }}
                                    />
                                </ErrorBoundary>
                            ) : activeView === 'research-radar' ? (
                                <ErrorBoundary fallbackTitle="资讯雷达加载异常">
                                    <ResearchRadarView
                                        onOpenStock={(stock) => {
                                            setSelectedStock(null);
                                            setSearchDetailStock(stock);
                                        }}
                                    />
                                </ErrorBoundary>
                            ) : activeView === 'themes' ? (
                                <ErrorBoundary fallbackTitle="题材热点加载异常">
                                    <ThemeHeatView
                                        onOpenStock={(stock) => {
                                            setSelectedStock(null);
                                            setSearchDetailStock(stock);
                                        }}
                                    />
                                </ErrorBoundary>
                            ) : activeView === 'templates' ? (
                                <ErrorBoundary fallbackTitle="策略模板加载异常">
                                    <StrategyTemplatesView />
                                </ErrorBoundary>
                            ) : activeView === 'ops' ? (
                                <ErrorBoundary fallbackTitle="专业驾驶舱加载异常">
                                    <OperationsCenter />
                                </ErrorBoundary>
                            ) : activeView === 'scanner' ? (
                                <>
                                    {/* Market Overview */}
                                    <section className="overflow-hidden rounded-[var(--radius-lg)] bg-[var(--sidebar)] px-4 py-4 text-white shadow-[var(--shadow-raised)] sm:px-5" aria-labelledby="market-risk-heading">
                                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                            <div className="flex min-w-0 flex-wrap items-center gap-3">
                                                <div>
                                                    <p className="text-[11px] font-semibold tracking-wide text-slate-400">
                                                        {marketRegime?.baseline_label || '市场决策基线'}
                                                    </p>
                                                    <h2 id="market-risk-heading" className="mt-0.5 text-base font-bold">
                                                        大盘风控
                                                    </h2>
                                                </div>
                                            {marketRegime && (
                                                <div className={cn(
                                                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold",
                                                    marketRegime.status === "CRITICAL" && "bg-rose-500/15 border-rose-300/30 text-rose-100",
                                                    marketRegime.status === "OFFENSIVE" && "bg-teal-400/15 border-teal-300/30 text-teal-100",
                                                    marketRegime.status === "DEFENSIVE" && "bg-amber-400/15 border-amber-300/30 text-amber-100"
                                                )}>
                                                    {marketRegime.status === "OFFENSIVE" ? "强力进攻" :
                                                     marketRegime.status === "CRITICAL" ? "严格防守" : "减仓观望"}
                                                </div>
                                            )}
                                            </div>
                                            {marketRegime && (
                                                <p className="max-w-3xl text-sm leading-relaxed text-slate-300 sm:text-end">
                                                    {marketRegime.desc}
                                                    <span className="mt-1 block text-xs text-slate-400">
                                                        {Object.entries(marketRegime.indices as Record<string, MarketPulseIndex>).map(([name, d]) =>
                                                            `${name === '创业' ? '创业板' : name} ${d.trend_label || (d.trend === 'BULL' ? '站上EMA20' : '低于EMA20')}｜当日${signedPct(d.chg_pct)}｜距EMA20 ${signedPct(d.ema20_gap_pct)}`
                                                        ).join(' · ')}
                                                    </span>
                                                </p>
                                            )}
                                        </div>
                                    </section>
                                    <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5" aria-label="主要市场指数">
                                        {marketLoading ? (
                                            Array(5).fill(0).map((_, i) => <MarketCard key={i} name="" price={0} pct={0} loading />)
                                        ) : (
                                            Object.entries(indices).map(([name, data]) => (
                                                <MarketCard key={name} name={name} price={data.price} pct={data.pct} />
                                            ))
                                        )}
                                    </section>
                                    
                                    <MarketSentiment />

                                    {/* Hot Sectors */}
                                    <SectorGrid sectors={sectors} />

                                    {lastScanSummary && !isScanning && viewMode !== 'history' && (
                                        <section
                                            className={cn(
                                                "rounded-2xl border px-4 py-3 shadow-sm",
                                                lastScanSummary.count > 0
                                                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                                                    : "border-amber-200 bg-amber-50 text-amber-800",
                                            )}
                                            role="status"
                                            aria-live="polite"
                                        >
                                            <p className="text-sm font-black">
                                                {lastScanSummary.strategyLabel}扫描完成
                                            </p>
                                            <p className="mt-1 text-xs font-semibold opacity-80">
                                                {lastScanSummary.matchMode === 'all' && lastScanSummary.strategyTypes.length > 1
                                                    ? `同时命中全部已选策略 ${lastScanSummary.count} 只；组合仅用于筛选，不新增买卖指令`
                                                    : lastScanSummary.strategyType === 'tv_dual' || lastScanSummary.strategyType === 'tv_dual_strict'
                                                    ? `当前信号 ${lastScanSummary.count} 只 · 历史复活 ${lastScanSummary.revivalCount} 只 · 动量观察 ${lastScanSummary.momentumCount} 只`
                                                    : ['high_tight_flag', 'turtle_breakout', 'limit_up_shakeout'].includes(lastScanSummary.strategyType)
                                                        ? `SHADOW研究命中 ${lastScanSummary.count} 只，不进入交易或Bark操作推送`
                                                    : `正式入选 ${lastScanSummary.count} 只`}
                                                {lastScanSummary.excludedCount > 0
                                                    ? `，已排除 ${lastScanSummary.excludedCount} 条其他策略或观察池结果。`
                                                    : '。'}
                                            </p>
                                            {(lastScanSummary.dataMode || lastScanSummary.dataDate) && (
                                                <p className="mt-1 text-xs font-semibold opacity-80">
                                                    数据：{lastScanSummary.dataMode === 'LIVE_SNAPSHOT' ? '实时行情' : '历史日K'}
                                                    {' · '}
                                                    {(lastScanSummary.asOf || lastScanSummary.dataDate || '').replace('T', ' ').slice(0, 19)}
                                                </p>
                                            )}
                                        </section>
                                    )}

                                    {!isScanning && viewMode !== 'history' && (
                                        resultStrategyType === 'tv_dual'
                                        || resultStrategyType === 'tv_dual_strict'
                                        || resultGroups.revival.length > 0
                                        || resultGroups.momentum.length > 0
                                    ) && (
                                        <div className="grid grid-cols-3 gap-2 rounded-2xl border border-slate-200 bg-slate-50 p-2" role="tablist" aria-label="扫描结果分类">
                                            {([
                                                ['formal', '当前策略信号', resultGroups.formal.length],
                                                ['revival', '历史信号复活', resultGroups.revival.length],
                                                ['momentum', '动量加速观察', resultGroups.momentum.length],
                                            ] as const).map(([group, label, count]) => (
                                                <button
                                                    key={group}
                                                    type="button"
                                                    role="tab"
                                                    aria-selected={activeResultGroup === group}
                                                    onClick={() => setActiveResultGroup(group)}
                                                    className={cn(
                                                        "rounded-xl px-3 py-2 text-xs font-bold transition-colors",
                                                        activeResultGroup === group
                                                            ? "bg-white text-blue-700 shadow-sm ring-1 ring-blue-100"
                                                            : "text-slate-500 hover:bg-white hover:text-slate-800",
                                                    )}
                                                >
                                                    {label} <span className="tabular-nums">{count}</span>
                                                </button>
                                            ))}
                                        </div>
                                    )}

                                    {/* Result Toolbar */}
                                    <section className="workspace-panel flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between" aria-labelledby="scan-results-heading">
                                        <div className="flex min-w-0 flex-wrap items-center gap-3">
                                            <div className="flex items-center gap-2 min-w-0">
                                                <h2 id="scan-results-heading" className="text-base font-bold text-slate-900">
                                                    {activeResultGroup === 'revival'
                                                        ? '历史信号复活'
                                                        : activeResultGroup === 'momentum'
                                                            ? '动量加速观察'
                                                            : lastScanSummary && viewMode !== 'history'
                                                                ? `${lastScanSummary.strategyLabel}当前信号`
                                                                : '扫描结果'}
                                                </h2>
                                                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold tabular-nums text-slate-600">{results.length} 只</span>
                                            </div>
                                            {/* History Selector */}
                                            {historyDates.length > 0 && (
                                                <div className="flex items-center gap-2 border-s border-slate-200 ps-3">
                                                    <label htmlFor="scan-history-date" className="metric-label flex items-center gap-2">
                                                        历史记录
                                                    </label>
                                                    <select
                                                        id="scan-history-date"
                                                        value={selectedDate}
                                                        onChange={(e) => loadHistory(e.target.value)}
                                                        disabled={isScanning}
                                                        className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 py-2 text-base font-medium text-slate-700 transition-[border-color,box-shadow] focus:border-blue-400 focus:outline-none focus:ring-4 focus:ring-blue-500/10 disabled:cursor-not-allowed disabled:opacity-50 sm:text-sm"
                                                    >
                                                        <option value="">选择日期</option>
                                                        {historyDates.map(date => (
                                                            <option key={date} value={date}>
                                                                {date} {date === new Date().toISOString().split('T')[0] ? "(今日扫描)" : ""}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex shrink-0 items-center gap-1 self-end rounded-xl border border-slate-200 bg-slate-50 p-1" role="group" aria-label="结果显示方式">
                                            <button
                                                type="button"
                                                onClick={() => setViewMode('list')}
                                                className={cn("inline-flex size-10 items-center justify-center rounded-lg transition-colors", viewMode === 'list' ? "bg-white text-blue-700 shadow-sm" : "text-slate-500 hover:bg-white hover:text-slate-800")}
                                                aria-label="使用列表视图"
                                                aria-pressed={viewMode === 'list'}
                                            >
                                                <List size={19} aria-hidden="true" />
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => setViewMode('grid')}
                                                className={cn("inline-flex size-10 items-center justify-center rounded-lg transition-colors", viewMode === 'grid' ? "bg-white text-blue-700 shadow-sm" : "text-slate-500 hover:bg-white hover:text-slate-800")}
                                                aria-label="使用网格视图"
                                                aria-pressed={viewMode === 'grid'}
                                            >
                                                <LayoutGrid size={19} aria-hidden="true" />
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => setViewMode('history')}
                                                className={cn("inline-flex size-10 items-center justify-center rounded-lg transition-colors", viewMode === 'history' ? "bg-white text-blue-700 shadow-sm" : "text-slate-500 hover:bg-white hover:text-slate-800")}
                                                aria-label="查看历史回溯"
                                                aria-pressed={viewMode === 'history'}
                                            >
                                                <Calendar size={19} aria-hidden="true" />
                                            </button>
                                            <div className="mx-1 h-6 w-px bg-slate-200" aria-hidden="true" />
                                            <button
                                                type="button"
                                                onClick={handleExport}
                                                className="inline-flex size-10 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-white hover:text-blue-700"
                                                aria-label="导出扫描结果"
                                            >
                                                <Download size={19} aria-hidden="true" />
                                            </button>
                                        </div>
                                    </section>

                                    {/* Results Section */}
                                    <div className="flex flex-col gap-3">
                                        {viewMode === 'history' ? (
                                            <ErrorBoundary fallbackTitle="历史回溯加载异常">
                                                <ScanHistoryView availableDates={availableDates.map(d => d.date)} />
                                            </ErrorBoundary>
                                        ) : results.length > 0 ? (
                                            <ErrorBoundary fallbackTitle="结果表格加载异常">
                                                <ResultsTable
                                                    results={results}
                                                    onSelectStock={setSelectedStock}
                                                    selectedCode={selectedStock?.代码}
                                                />
                                            </ErrorBoundary>
                                        ) : (
                                            <div className="glass-card flex min-h-[280px] flex-col items-center justify-center border-dashed p-6 text-center text-slate-500 sm:p-10" role="status" aria-live="polite">
                                                {isScanning ? (
                                                    <div className="flex w-full max-w-md flex-col items-center">
                                                        <div className="mb-5 flex size-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-700">
                                                            <Zap size={28} aria-hidden="true" />
                                                        </div>
                                                        <p className="mb-2 text-lg font-bold text-slate-800">正在分析全市场个股…</p>
                                                        {scanProgress ? (
                                                            <div className="w-full mt-4">
                                                                <div className="mb-2 flex justify-between gap-3 text-xs font-semibold text-slate-500">
                                                                    <span>{scanProgress.message || '正在连接分析引擎…'}</span>
                                                                    <span className="font-mono tabular-nums">{scanProgress.current}/{scanProgress.total}</span>
                                                                </div>
                                                                <div
                                                                    className="mb-2 h-2 w-full overflow-hidden rounded-full bg-slate-100"
                                                                    role="progressbar"
                                                                    aria-label="市场扫描进度"
                                                                    aria-valuemin={0}
                                                                    aria-valuemax={scanProgress.total}
                                                                    aria-valuenow={scanProgress.current}
                                                                >
                                                                    <div 
                                                                        className="h-2 rounded-full bg-blue-700 transition-[width] duration-300"
                                                                        style={{ width: `${scanProgress.total > 0 ? (scanProgress.current / scanProgress.total) * 100 : 0}%` }}
                                                                    />
                                                                </div>
                                                                <div className="mt-4 text-center text-xs text-slate-500">
                                                                    已耗时 <span className="font-mono font-semibold tabular-nums text-blue-700">{scanProgress.elapsed}s</span>
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <p className="mt-1 text-sm">正在准备分析引擎…</p>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <>
                                                        <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-slate-100 text-slate-500">
                                                            <Search size={28} strokeWidth={1.5} aria-hidden="true" />
                                                        </div>
                                                        <p className="text-lg font-bold text-slate-800">
                                                            {lastScanSummary
                                                                ? activeResultGroup === 'revival'
                                                                    ? '本次没有历史复活候选'
                                                                    : activeResultGroup === 'momentum'
                                                                        ? '本次没有动量观察候选'
                                                                        : `${lastScanSummary.strategyLabel}本次没有当前信号`
                                                                : '准备发现下一批候选股'}
                                                        </p>
                                                        <p className="mt-1 max-w-md text-sm text-slate-500">
                                                            {lastScanSummary
                                                                ? '结果保持为空，不会用其他策略或历史扫描的股票代替。'
                                                                : '运行全市场扫描，使用当前策略参数生成候选股与风险证据。'}
                                                        </p>
                                                        <button type="button" onClick={startScan} className="primary-button mt-5">
                                                            <Zap size={17} aria-hidden="true" />
                                                            开始市场扫描
                                                        </button>
                                                    </>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </>
                            ) : activeView === 'paper' ? (
                                <ErrorBoundary fallbackTitle="模拟盘加载异常">
                                    <PaperTradingView />
                                </ErrorBoundary>
                            ) : activeView === 'alerts' ? (
                                <ErrorBoundary fallbackTitle="预警页加载异常">
                                    <AlertsView />
                                </ErrorBoundary>
                            ) : activeView === 'settings' ? (
                                <ErrorBoundary fallbackTitle="设置页加载异常">
                                    <SettingsView />
                                </ErrorBoundary>
                            ) : (
                                <ErrorBoundary fallbackTitle="系统概览加载异常">
                                    <OverviewView onNavigate={setActiveView} />
                                </ErrorBoundary>
                            )}
                        </div>

                        {/* Global Sidebar: AI Deep Dive (K-Line) */}
                        {selectedStock && (
                            <aside className="h-fit w-full xl:sticky xl:top-0 xl:w-[400px] xl:shrink-0" aria-label="个股深度分析">
                                <ErrorBoundary fallbackTitle="AI 深度分析加载异常">
                                    <AIDeepDive
                                        stock={selectedStock}
                                        onClose={() => setSelectedStock(null)}
                                    />
                                </ErrorBoundary>
                            </aside>
                        )}
                    </div>
                </main>
            </div>

            <FilterModal
                isOpen={isFilterOpen}
                onClose={() => setIsFilterOpen(false)}
                params={params}
                setParams={setParams}
                onScan={startScan}
                availableDates={availableDates}
            />
        </div>
    );
}
