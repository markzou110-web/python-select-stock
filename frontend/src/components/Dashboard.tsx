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
import { Play, Filter, Download, LayoutGrid, List, Search, Loader2, Zap, Calendar } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useScanStore } from '@/stores/scanStore';
import { useMarketStore } from '@/stores/marketStore';

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

    // ── Scan store ──
    const results = useScanStore(s => s.results);
    const isScanning = useScanStore(s => s.isScanning);
    const scanProgress = useScanStore(s => s.scanProgress);
    const selectedStock = useScanStore(s => s.selectedStock);
    const params = useScanStore(s => s.params);
    const historyDates = useScanStore(s => s.historyDates);
    const selectedDate = useScanStore(s => s.selectedDate);
    const availableDates = useScanStore(s => s.availableDates);
    const viewMode = useScanStore(s => s.viewMode);
    const isFilterOpen = useScanStore(s => s.isFilterOpen);
    const setSelectedStock = useScanStore(s => s.setSelectedStock);
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

    // ── Initialization ──
    useEffect(() => {
        const init = async () => {
            useMarketStore.setState({ loading: true });
            const now = new Date();
            const dateStr = now.toISOString().split('T')[0];
            const timeStr = now.toLocaleTimeString();
            setLastUpdated(`${dateStr} ${timeStr}`);
            await Promise.all([
                fetchMarketData(), 
                fetchSyncStatus(), 
                fetchHistory(), 
                fetchAvailableDates(),
                fetchMarketPulse()
            ]);
            useMarketStore.setState({ loading: false });
        };
        init();

        const intervalId = setInterval(fetchSyncStatus, 5000);
        return () => clearInterval(intervalId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <div className="flex h-screen overflow-hidden w-full bg-slate-100">
            <Sidebar
                syncProgress={syncProgress}
                onStartSync={startSync}
                onStartSyncFundamentals={startSyncFundamentals}
                onStopSync={stopSync}
                activeView={activeView}
                onNavigate={setActiveView}
            />
            <div className="flex-1 flex flex-col min-h-0 overflow-y-auto bg-slate-100">
                <Header
                    onScan={startScan}
                    loading={isScanning}
                    lastUpdated={lastUpdated}
                    onOpenFilters={() => setIsFilterOpen(true)}
                    onSelectStock={(stock) => {
                        setSelectedStock(stock);
                        if (activeView !== 'scanner' && activeView !== 'paper') {
                            setActiveView('scanner');
                        }
                    }}
                />

                <div className="flex-1 overflow-y-auto px-6 py-5">
                    <div className="flex gap-5 items-start">
                        <div className={cn("transition-all duration-300 space-y-6", selectedStock ? "flex-1 min-w-0" : "w-full")}>
                            {activeView === 'overview' ? (
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
                            ) : activeView === 'watchlist' ? (
                                <ErrorBoundary fallbackTitle="观察池加载异常">
                                    <WatchlistView />
                                </ErrorBoundary>
                            ) : activeView === 'templates' ? (
                                <ErrorBoundary fallbackTitle="策略模板加载异常">
                                    <StrategyTemplatesView />
                                </ErrorBoundary>
                            ) : activeView === 'scanner' ? (
                                <>
                                    {/* Market Overview */}
                                    <div className="workspace-panel px-4 py-3 flex items-center justify-between">
                                        <div className="flex items-center gap-3 min-w-0">
                                            <h2 className="text-sm font-black text-slate-900 uppercase tracking-widest flex items-center gap-2">
                                                大盘多因子风控
                                            </h2>
                                            {marketRegime && (
                                                <div className={cn(
                                                    "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-black border",
                                                    marketRegime.status === "CRITICAL" && "bg-rose-50 border-rose-200 text-rose-700",
                                                    marketRegime.status === "OFFENSIVE" && "bg-teal-50 border-teal-200 text-teal-700",
                                                    marketRegime.status === "DEFENSIVE" && "bg-amber-50 border-amber-200 text-amber-700"
                                                )}>
                                                    {marketRegime.status === "OFFENSIVE" ? "强力进攻" :
                                                     marketRegime.status === "CRITICAL" ? "严格防守" : "减仓观望"}
                                                </div>
                                            )}
                                        </div>
                                        {marketRegime && (
                                            <span className="text-[10px] text-slate-500 font-bold truncate">
                                                {marketRegime.desc} | {Object.entries(marketRegime.indices).map(([name, d]: [any, any]) => 
                                                    `${name}: ${d.trend === 'BULL' ? '多头' : '空头'} `
                                                )}
                                            </span>
                                        )}
                                    </div>
                                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3">
                                        {marketLoading ? (
                                            Array(5).fill(0).map((_, i) => <MarketCard key={i} name="" price={0} pct={0} loading />)
                                        ) : (
                                            Object.entries(indices).map(([name, data]: [string, any]) => (
                                                <MarketCard key={name} name={name} price={data.price} pct={data.pct} />
                                            ))
                                        )}
                                    </div>
                                    
                                    <div>
                                        <MarketSentiment />
                                    </div>

                                    {/* Hot Sectors */}
                                    <SectorGrid sectors={sectors} />

                                    {/* Scan Actions & Filters */}
                                    <div className="workspace-panel p-3 flex items-center justify-between gap-4">
                                        <div className="flex items-center gap-3 flex-wrap">
                                            <button
                                                onClick={startScan}
                                                disabled={isScanning}
                                                className="primary-button"
                                            >
                                                {isScanning ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                                                {isScanning ? '正在扫描全市场...' : '开始全市场扫描'}
                                            </button>
                                            <button
                                                onClick={() => setIsFilterOpen(true)}
                                                className="toolbar-button"
                                            >
                                                <Filter size={18} />
                                                策略参数配置
                                            </button>

                                            {/* History Selector */}
                                            {historyDates.length > 0 && (
                                                <div className="flex items-center gap-2 pl-3 border-l border-slate-200">
                                                    <span className="metric-label flex items-center gap-2">
                                                        历史记录
                                                    </span>
                                                    <select
                                                        value={selectedDate}
                                                        onChange={(e) => loadHistory(e.target.value)}
                                                        className="bg-white border border-slate-200 text-slate-700 text-xs font-bold rounded-md px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-300 transition-all cursor-pointer"
                                                    >
                                                        <option value="">-- 选择记录日期 --</option>
                                                        {historyDates.map(date => (
                                                            <option key={date} value={date}>
                                                                {date} {date === new Date().toISOString().split('T')[0] ? "(今日扫描)" : ""}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex items-center gap-1 bg-slate-50 p-1 rounded-lg border border-slate-200 shrink-0">
                                            <button
                                                onClick={() => setViewMode('list')}
                                                className={cn("p-2 rounded-md transition-colors", viewMode === 'list' ? "bg-white text-blue-700 shadow-sm" : "text-slate-400 hover:text-slate-600")}
                                            >
                                                <List size={20} />
                                            </button>
                                            <button
                                                onClick={() => setViewMode('grid')}
                                                className={cn("p-2 rounded-md transition-colors", viewMode === 'grid' ? "bg-white text-blue-700 shadow-sm" : "text-slate-400 hover:text-slate-600")}
                                            >
                                                <LayoutGrid size={20} />
                                            </button>
                                            <button
                                                onClick={() => setViewMode('history')}
                                                className={cn("p-2 rounded-md transition-colors flex items-center gap-1.5", viewMode === 'history' ? "bg-white text-blue-700 shadow-sm" : "text-slate-400 hover:text-slate-600")}
                                                title="历史回溯"
                                            >
                                                <Calendar size={20} />
                                            </button>
                                            <div className="mx-1 h-6 w-[1px] bg-slate-200" />
                                            <button
                                                onClick={handleExport}
                                                className="p-2 text-slate-400 hover:text-blue-700 hover:bg-white rounded-md transition-colors"
                                            >
                                                <Download size={20} />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Results Section */}
                                    <div className="flex flex-col gap-5">
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
                                            <div className="glass-card min-h-[360px] flex flex-col items-center justify-center text-slate-400 p-16 border-dashed border">
                                                {isScanning ? (
                                                    <div className="flex flex-col items-center animate-pulse w-full max-w-md">
                                                        <div className="w-14 h-14 bg-blue-50 text-blue-700 rounded-lg flex items-center justify-center mb-5">
                                                            <Zap size={32} />
                                                        </div>
                                                        <p className="font-bold text-lg text-slate-600 mb-2">正在分析全市场个股...</p>
                                                        {scanProgress ? (
                                                            <div className="w-full mt-4">
                                                                <div className="flex justify-between text-xs font-bold text-slate-500 mb-2">
                                                                    <span>{scanProgress.message || '引擎连线中...'}</span>
                                                                    <span className="font-mono">{scanProgress.current}/{scanProgress.total}</span>
                                                                </div>
                                                                <div className="w-full bg-slate-100 rounded-full h-2 mb-2 overflow-hidden">
                                                                    <div 
                                                                        className="bg-blue-700 h-2 rounded-full transition-all duration-300"
                                                                        style={{ width: `${scanProgress.total > 0 ? (scanProgress.current / scanProgress.total) * 100 : 0}%` }}
                                                                    />
                                                                </div>
                                                                <div className="text-center text-[10px] text-slate-400 font-mono tracking-widest uppercase mt-4">
                                                                    已耗时: <span className="text-blue-700 font-bold">{scanProgress.elapsed}s</span>
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <p className="text-sm font-medium mt-1">引擎启动中，准备建立通道...</p>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <>
                                                        <div className="w-14 h-14 bg-slate-100 rounded-lg flex items-center justify-center mb-4">
                                                            <Search size={32} strokeWidth={1.5} />
                                                        </div>
                                                        <p className="font-bold text-lg text-slate-600">暂无扫描结果</p>
                                                        <p className="text-sm font-medium mt-1">点击上方按钮启动多因子共振突破分析</p>
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
                            <div className="w-[430px] sticky top-0 animate-in slide-in-from-right-8 duration-300 h-fit">
                                <ErrorBoundary fallbackTitle="AI 深度分析加载异常">
                                    <AIDeepDive
                                        stock={selectedStock}
                                        onClose={() => setSelectedStock(null)}
                                    />
                                </ErrorBoundary>
                            </div>
                        )}
                    </div>
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
        </div>
    );
}
