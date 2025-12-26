"use client";

import React, { useState, useEffect } from 'react';
import Header from '@/components/Header';
import MarketCard from '@/components/MarketCard';
import SectorGrid from '@/components/SectorGrid';
import FilterModal from '@/components/FilterModal';
import ResultsTable from '@/components/ResultsTable';
import { marketApi } from '@/lib/api';
import { Play, Filter, Download, LayoutGrid, List, Search, Loader2, Zap } from 'lucide-react';

export default function Dashboard() {
    const [indices, setIndices] = useState<any>({});
    const [sectors, setSectors] = useState<any[]>([]);
    const [results, setResults] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [isScanning, setIsScanning] = useState(false);
    const [isFilterOpen, setIsFilterOpen] = useState(false);

    const [params, setParams] = useState({
        threshold: 0.12,
        vol_multiplier: 1.5,
        rsi_min: 55,
        use_macd_zero: true,
        use_bb_sqz: true,
        sqz_lookback: 10,
        use_weekly: true,
        market_range: "包含科创板",
        turnover_min: 3.0
    });

    useEffect(() => {
        async function fetchData() {
            try {
                const [idxRes, secRes] = await Promise.all([
                    marketApi.getIndices(),
                    marketApi.getSectors()
                ]);
                setIndices(idxRes.data);
                setSectors(secRes.data);
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        }
        fetchData();
    }, []);

    const handleScan = async () => {
        setIsScanning(true);
        setResults([]);
        try {
            const res = await marketApi.scanMarket(params);
            setResults(res.data);
        } catch (e: any) {
            console.error("Scan Error Detail:", e);
            const errorMsg = e.response?.data?.detail || e.message;
            alert(`扫描失败: ${errorMsg}\n\n请检查后端终端输出或网络连接。`);
        } finally {
            setIsScanning(false);
        }
    };

    return (
        <main className="flex-1 flex flex-col overflow-hidden bg-[#F1F5F9]/30">
            <Header />

            <div className="flex-1 overflow-y-auto px-8 pb-10 space-y-10">
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
                            className="flex items-center gap-2 px-6 py-3 premium-gradient text-white rounded-2xl font-bold shadow-xl shadow-indigo-100 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 disabled:scale-100"
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
                    </div>

                    <div className="flex items-center gap-2 bg-white p-1.5 rounded-2xl border border-slate-200">
                        <button className="p-2 bg-indigo-50 text-indigo-600 rounded-xl"><List size={20} /></button>
                        <button className="p-2 text-slate-400 hover:text-slate-600 rounded-xl"><LayoutGrid size={20} /></button>
                        <div className="mx-1 h-6 w-[1px] bg-slate-200" />
                        <button className="p-2 text-slate-400 hover:text-slate-600 rounded-xl"><Download size={20} /></button>
                    </div>
                </div>

                {/* Results Section */}
                {results.length > 0 ? (
                    <ResultsTable results={results} />
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

            <FilterModal
                isOpen={isFilterOpen}
                onClose={() => setIsFilterOpen(false)}
                params={params}
                setParams={setParams}
                onScan={handleScan}
            />
        </main>
    );
}
