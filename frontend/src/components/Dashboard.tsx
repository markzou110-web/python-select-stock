"use client";

import React, { useState, useEffect, useCallback } from 'react';
import Header from '@/components/Header';
import Sidebar from '@/components/Sidebar';
import MarketCard from '@/components/MarketCard';
import SectorGrid from '@/components/SectorGrid';
import FilterModal from '@/components/FilterModal';
import ResultsTable from '@/components/ResultsTable';
import AIDeepDive from '@/components/AIDeepDive';
import PaperTradingView from '@/components/PaperTradingView';
import SettingsView from '@/components/SettingsView';
import { marketApi } from '@/lib/api';
import api from '@/lib/api';
import { Play, Filter, Download, LayoutGrid, List, Search, Loader2, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function Dashboard() {
    const [indices, setIndices] = useState<any>({});
    const [sectors, setSectors] = useState<any[]>([]);
    const [results, setResults] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [isScanning, setIsScanning] = useState(false);
    const [selectedStock, setSelectedStock] = useState<any>(null);
    const [activeView, setActiveView] = useState('scanner');
    const [isFilterOpen, setIsFilterOpen] = useState(false);
    const [lastUpdated, setLastUpdated] = useState("");

    const [params, setParams] = useState({
        threshold: 0.12,
        vol_multiplier: 1.5,
        rsi_min: 55,
        use_macd_filter: true,
        use_bb_sqz: true,
        sqz_lookback: 10,
        use_weekly: true,
        market_range: "全市场(除科创)",
        turnover_min: 3.0,
        mkt_cap_min: 0,
        use_rs_filter: true,
        local_only: true
    });

    const [historyDates, setHistoryDates] = useState<string[]>([]);
    const [selectedDate, setSelectedDate] = useState<string>("");

    const [syncProgress, setSyncProgress] = useState<any>(null);

    const fetchMarketData = useCallback(async () => {
        // Try to load from localStorage first for instant rendering
        try {
            const cachedIndices = localStorage.getItem('av_indices_cache');
            const cachedSectors = localStorage.getItem('av_sectors_cache');
            if (cachedIndices) setIndices(JSON.parse(cachedIndices));
            if (cachedSectors) setSectors(JSON.parse(cachedSectors));
        } catch (e) {
            console.error("Local cache error:", e);
        }

        try {
            const [idxRes, secRes] = await Promise.all([
                marketApi.getIndices(),
                marketApi.getSectors()
            ]);
            setIndices(idxRes.data);
            setSectors(secRes.data);

            // Update cache
            localStorage.setItem('av_indices_cache', JSON.stringify(idxRes.data));
            localStorage.setItem('av_sectors_cache', JSON.stringify(secRes.data));
        } catch (e) {
            console.error("Failed to fetch market data:", e);
        }
    }, []);

    const fetchSyncStatus = useCallback(async () => {
        try {
            const res = await api.get('/api/sync/status');
            setSyncProgress(res.data);
        } catch (e) {
            console.error("Sync status fetch failed", e);
        }
    }, []);

    const fetchHistory = useCallback(async () => {
        try {
            const dateRes = await api.get('/api/scan/dates');
            const dates = dateRes.data;
            setHistoryDates(dates);

            // 如果有历史记录且当前没有选中，则默认加载最近一天的
            if (dates.length > 0 && !results.length) {
                const latestDate = dates[0];
                setSelectedDate(latestDate);
                const res = await api.get(`/api/scan/history?date=${latestDate}`);
                setResults(res.data);
            }
        } catch (e) {
            console.error("Failed to fetch history", e);
        }
    }, [results.length]);

    useEffect(() => {
        const init = async () => {
            setLoading(true);
            setLastUpdated(new Date().toLocaleTimeString());
            await Promise.all([fetchMarketData(), fetchSyncStatus(), fetchHistory()]);
            setLoading(false);
        };
        init();

        const intervalId = setInterval(fetchSyncStatus, 5000);
        return () => clearInterval(intervalId);
    }, [fetchMarketData, fetchSyncStatus, fetchHistory]);

    const startSync = async () => {
        try {
            await api.post('/api/sync/daily');
            fetchSyncStatus(); // Initial check
        } catch (e) {
            console.error("Sync start failed", e);
        }
    };

    const handleScan = async () => {
        setIsScanning(true);
        setResults([]);
        try {
            const res = await marketApi.scanMarket(params);
            setResults(res.data);
            setSelectedDate(new Date().toISOString().split('T')[0]);
            fetchHistory(); // 刷新日期列表
        } catch (e: any) {
            console.error("Scan Error Detail:", e);
            const errorMsg = e.response?.data?.detail || e.message;
            alert(`扫描失败: ${errorMsg}\n\n请检查后端终端输出或网络连接。`);
        } finally {
            setIsScanning(false);
        }
    };
    const handleDateChange = async (date: string) => {
        setSelectedDate(date);
        if (!date) return;
        setIsScanning(true);
        try {
            const res = await api.get(`/api/scan/history?date=${date}`);
            setResults(res.data);
        } catch (e) {
            console.error("Failed to load history", e);
        } finally {
            setIsScanning(false);
        }
    };

    const [viewMode, setViewMode] = useState<'list' | 'grid'>('list');

    const handleExport = () => {
        if (results.length === 0) return;
        const headers = ["代码", "名称", "行业", "现价", "涨幅%", "综合强度", "RSI", "DIF", "BB", "粘合度", "历史胜率"];
        const rows = results.map(r => [
            r.代码, r.名称, r.行业, r.现价, r["涨幅%"], r.Score, r.RSI, r.DIF, r.BB, r.粘合度, r.历史胜率
        ]);
        const csvContent = [headers, ...rows].map(e => e.join(",")).join("\n");
        const blob = new Blob(["\ufeff" + csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", `AlphaVision_Results_${new Date().toLocaleDateString()}.csv`);
        link.click();
    };

    return (
        <div className="flex h-screen overflow-hidden w-full">
            <Sidebar
                syncProgress={syncProgress}
                onStartSync={startSync}
                activeView={activeView}
                onNavigate={setActiveView}
            />
            <div className="flex-1 flex flex-col min-h-0 overflow-y-auto bg-slate-50">
                <Header
                    onScan={handleScan}
                    loading={isScanning}
                    lastUpdated={lastUpdated}
                    onOpenFilters={() => setIsFilterOpen(true)}
                />

                <div className="flex-1 overflow-y-auto px-8 pb-10 space-y-10">
                    {activeView === 'scanner' ? (
                        <>
                            {/* Market Overview */}
                            <div className="flex gap-6 overflow-x-auto pb-2 scrollbar-none">
                                {loading ? (
                                    Array(5).fill(0).map((_, i) => <MarketCard key={i} name="" price={0} pct={0} loading />)
                                ) : (
                                    Object.entries(indices).map(([name, data]: [string, any]) => (
                                        <MarketCard key={name} name={name} price={data.price} pct={data.pct} />
                                    ))
                                )}
                            </div>

                            {/* Hot Sectors */}
                            <SectorGrid sectors={sectors} />

                            {/* Scan Actions & Filters */}
                            <div className="flex items-center justify-between pt-4 border-t border-slate-200">
                                <div className="flex items-center gap-4">
                                    <button
                                        onClick={handleScan}
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
                                                onChange={(e) => handleDateChange(e.target.value)}
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
                            <div className="flex gap-8 items-start">
                                <div className={cn("transition-all duration-500", selectedStock ? "flex-1 min-w-0" : "w-full")}>
                                    {results.length > 0 ? (
                                        <ResultsTable
                                            results={results}
                                            onSelectStock={setSelectedStock}
                                            selectedCode={selectedStock?.代码}
                                        />
                                    ) : (
                                        <div className="glass-card min-h-[400px] flex flex-col items-center justify-center text-slate-400 p-20 border-dashed border-2">
                                            {isScanning ? (
                                                <div className="flex flex-col items-center animate-pulse">
                                                    <div className="w-16 h-16 bg-indigo-50 text-indigo-500 rounded-full flex items-center justify-center mb-6">
                                                        <Zap size={32} />
                                                    </div>
                                                    <p className="font-bold text-lg text-slate-600">正在分析全市场个股...</p>
                                                    <p className="text-sm font-medium mt-1">多因子共振引擎正在进行深度过滤</p>
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

                                {selectedStock && (
                                    <div className="w-96 sticky top-0 animate-in slide-in-from-right-8 duration-500">
                                        <AIDeepDive
                                            stock={selectedStock}
                                            onClose={() => setSelectedStock(null)}
                                        />
                                    </div>
                                )}
                            </div>
                        </>
                    ) : activeView === 'paper' ? (
                        <PaperTradingView />
                    ) : (
                        <SettingsView />
                    )}
                </div>

                <FilterModal
                    isOpen={isFilterOpen}
                    onClose={() => setIsFilterOpen(false)}
                    params={params}
                    setParams={setParams}
                    onScan={handleScan}
                />
            </div>
        </div>
    );
}
