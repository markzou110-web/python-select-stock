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
        <div className="flex h-screen overflow-hidden w-full">
            <Sidebar
                syncProgress={syncProgress}
                onStartSync={startSync}
                onStartSyncFundamentals={startSyncFundamentals}
                onStopSync={stopSync}
                activeView={activeView}
                onNavigate={setActiveView}
            />
            <div className="flex-1 flex flex-col min-h-0 overflow-y-auto bg-slate-50">
                <Header
                    onScan={startScan}
                    loading={isScanning}
                    lastUpdated={lastUpdated}
                    onOpenFilters={() => setIsFilterOpen(true)}
                />

                <div className="flex-1 overflow-y-auto px-8 pb-10">
                    <div className="flex gap-8 items-start">
                        <div className={cn("transition-all duration-500 space-y-10", selectedStock ? "flex-1 min-w-0" : "w-full")}>
                            {activeView === 'scanner' ? (
                                <>
                                    {/* Market Overview */}
                                    <div className="flex items-center gap-4 mb-2">
                                        <h2 className="text-sm font-extrabold text-slate-800 uppercase tracking-widest flex items-center gap-2">
                                            大盘多因子风控
                                            {marketRegime && (
                                                <div className={cn(
                                                    "inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border shadow-sm ml-2",
                                                    marketRegime.status === "CRITICAL" && "bg-rose-50 border-rose-100 text-rose-700 shadow-rose-100/50",
                                                    marketRegime.status === "OFFENSIVE" && "bg-emerald-50 border-emerald-100 text-emerald-700 shadow-emerald-100/50",
                                                    marketRegime.status === "DEFENSIVE" && "bg-amber-50 border-amber-100 text-amber-700 shadow-amber-100/50"
                                                )}>
                                                    {marketRegime.status === "OFFENSIVE" ? "🚀 强力进攻" : 
                                                     marketRegime.status === "CRITICAL" ? "🛡️ 严格防守" : "🚧 减仓观望"}
                                                </div>
                                            )}
                                        </h2>
                                        {marketRegime && (
                                            <span className="text-[10px] text-slate-400 font-medium">
                                                {marketRegime.desc} | {Object.entries(marketRegime.indices).map(([name, d]: [any, any]) => 
                                                    `${name}: ${d.trend === 'BULL' ? '🟢' : '🔴'} `
                                                )}
                                            </span>
                                        )}
                                    </div>
                                    <div className="flex gap-6 overflow-x-auto pb-2 scrollbar-none">
                                        {marketLoading ? (
                                            Array(5).fill(0).map((_, i) => <MarketCard key={i} name="" price={0} pct={0} loading />)
                                        ) : (
                                            Object.entries(indices).map(([name, data]: [string, any]) => (
                                                <MarketCard key={name} name={name} price={data.price} pct={data.pct} />
                                            ))
                                        )}
                                    </div>
                                    
                                    <div className="my-6">
                                        <MarketSentiment />
                                    </div>

                                    {/* Hot Sectors */}
                                    <SectorGrid sectors={sectors} />

                                    {/* Scan Actions & Filters */}
                                    <div className="flex items-center justify-between pt-4 border-t border-slate-200">
                                        <div className="flex items-center gap-4">
                                            <button
                                                onClick={startScan}
                                                disabled={isScanning}
                                                className={cn(
                                                    "flex items-center gap-2 px-6 py-3 premium-gradient text-white rounded-2xl font-bold shadow-xl shadow-indigo-100 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 disabled:scale-100"
                                                )}
                                            >
                                                {isScanning ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                                                {isScanning ? '正在扫描全市场...' : '开始全市场扫描'}
                                            </button>
                                            <div className="h-10 w-[1px] bg-slate-200 mx-2" />
                                            <button
                                                onClick={() => setIsFilterOpen(true)}
                                                className="flex items-center gap-2 px-4 py-3 bg-white border border-slate-200 rounded-2xl font-bold text-slate-600 hover:bg-slate-50 transition-all"
                                            >
                                                <Filter size={18} />
                                                策略参数配置
                                            </button>

                                            {/* History Selector */}
                                            {historyDates.length > 0 && (
                                                <div className="flex items-center gap-3 pl-6 border-l border-slate-200 ml-2">
                                                    <span className="text-xs font-bold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                                                        🕒 历史记录
                                                    </span>
                                                    <select
                                                        value={selectedDate}
                                                        onChange={(e) => loadHistory(e.target.value)}
                                                        className="bg-white border border-slate-200 text-slate-600 text-sm font-bold rounded-xl px-4 py-2.5 outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all cursor-pointer shadow-sm hover:border-slate-300"
                                                    >
                                                        <option value="">-- 选择记录日期 --</option>
                                                        {historyDates.map(date => (
                                                            <option key={date} value={date}>
                                                                📅 {date} {date === new Date().toISOString().split('T')[0] ? "(今日扫描)" : ""}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex items-center gap-2 bg-white p-1.5 rounded-2xl border border-slate-200">
                                            <button
                                                onClick={() => setViewMode('list')}
                                                className={cn("p-2 rounded-xl transition-all", viewMode === 'list' ? "bg-indigo-50 text-indigo-600" : "text-slate-400 hover:text-slate-600")}
                                            >
                                                <List size={20} />
                                            </button>
                                            <button
                                                onClick={() => setViewMode('grid')}
                                                className={cn("p-2 rounded-xl transition-all", viewMode === 'grid' ? "bg-indigo-50 text-indigo-600" : "text-slate-400 hover:text-slate-600")}
                                            >
                                                <LayoutGrid size={20} />
                                            </button>
                                            <button
                                                onClick={() => setViewMode('history')}
                                                className={cn("p-2 rounded-xl transition-all flex items-center gap-1.5", viewMode === 'history' ? "bg-indigo-50 text-indigo-600" : "text-slate-400 hover:text-slate-600")}
                                                title="历史回溯"
                                            >
                                                <Calendar size={20} />
                                            </button>
                                            <div className="mx-1 h-6 w-[1px] bg-slate-200" />
                                            <button
                                                onClick={handleExport}
                                                className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all"
                                            >
                                                <Download size={20} />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Results Section */}
                                    <div className="flex flex-col gap-8">
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
                                            <div className="glass-card min-h-[400px] flex flex-col items-center justify-center text-slate-400 p-20 border-dashed border-2">
                                                {isScanning ? (
                                                    <div className="flex flex-col items-center animate-pulse w-full max-w-md">
                                                        <div className="w-16 h-16 bg-indigo-50 text-indigo-500 rounded-full flex items-center justify-center mb-6 shadow-indigo-100 shadow-xl">
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
                                                                        className="premium-gradient h-2 rounded-full transition-all duration-300"
                                                                        style={{ width: `${scanProgress.total > 0 ? (scanProgress.current / scanProgress.total) * 100 : 0}%` }}
                                                                    />
                                                                </div>
                                                                <div className="text-center text-[10px] text-slate-400 font-mono tracking-widest uppercase mt-4">
                                                                    已耗时: <span className="text-indigo-500 font-bold">{scanProgress.elapsed}s</span>
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <p className="text-sm font-medium mt-1">引擎启动中，准备建立通道...</p>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <>
                                                        <div className="w-16 h-16 bg-slate-100 rounded-2xl flex items-center justify-center mb-4">
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
                            ) : (
                                <ErrorBoundary fallbackTitle="设置页加载异常">
                                    <SettingsView />
                                </ErrorBoundary>
                            )}
                        </div>

                        {/* Global Sidebar: AI Deep Dive (K-Line) */}
                        {selectedStock && (
                            <div className="w-[450px] sticky top-0 animate-in slide-in-from-right-8 duration-500 h-fit">
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
