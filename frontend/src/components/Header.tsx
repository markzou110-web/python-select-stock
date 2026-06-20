"use client";

import React, { useState, useEffect, useRef } from 'react';
import { Search, Play, Loader2, Clock, X, ArrowRight, Star, SlidersHorizontal } from 'lucide-react';
import api from '@/lib/api';

interface HeaderProps {
    onScan: () => void;
    loading: boolean;
    lastUpdated: string;
    onOpenFilters: () => void;
    onSelectStock: (stock: StockSearchResult) => void;
}

interface StockSearchResult {
    code: string;
    name: string;
    industry: string;
}

export default function Header({ onScan, loading, lastUpdated, onOpenFilters, onSelectStock }: HeaderProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [results, setResults] = useState<StockSearchResult[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const [showDropdown, setShowDropdown] = useState(false);
    const [addingWatchCode, setAddingWatchCode] = useState<string | null>(null);
    const [notice, setNotice] = useState<string>('');
    const dropdownRef = useRef<HTMLDivElement>(null);
    const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    // Close dropdown on click outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setShowDropdown(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    // Fetch stock search results on query change
    useEffect(() => {
        if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
        }

        if (!searchQuery.trim()) {
            setResults([]);
            setIsSearching(false);
            return;
        }

        setIsSearching(true);
        searchTimeoutRef.current = setTimeout(async () => {
            try {
                const res = await api.get(`/api/stock/search?query=${encodeURIComponent(searchQuery.trim())}`);
                setResults(res.data || []);
                setShowDropdown(true);
            } catch (err) {
                console.error("Fuzzy Search Error:", err);
            } finally {
                setIsSearching(false);
            }
        }, 200); // 200ms debounce

        return () => {
            if (searchTimeoutRef.current) {
                clearTimeout(searchTimeoutRef.current);
            }
        };
    }, [searchQuery]);

    const handleSelectStock = (stock: StockSearchResult) => {
        setShowDropdown(false);
        setSearchQuery('');
        onSelectStock(stock);
    };

    const addToWatchlist = async (stock: StockSearchResult) => {
        setAddingWatchCode(stock.code);
        setNotice('');
        try {
            const detail = await api.get(`/api/stock/detail?code=${stock.code}`);
            const info = detail.data?.stock_info || {};
            await api.post('/api/watchlist/add', {
                code: stock.code,
                name: stock.name,
                industry: stock.industry,
                watch_price: info.现价 || info.current_price || 1,
                strategy_type: info.strategy_type || info.chart_strategy_type || 'squeeze',
                reason: '顶部搜索加入观察池',
                source: 'header_search',
            });
            setNotice(`${stock.name} 已加入观察池`);
        } catch (err: unknown) {
            console.error("Header add watchlist error:", err);
            const message = typeof err === 'object' && err !== null && 'response' in err
                ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
                : undefined;
            setNotice(message || '加入观察池失败');
        } finally {
            setAddingWatchCode(null);
        }
    };

    return (
        <header className="flex items-center justify-between gap-4 px-5 py-3 border-b border-slate-300/80 bg-[#fbfaf6]/95">
            <div className="min-w-0">
                <h2 className="text-base font-black text-slate-950 tracking-tight">
                    Alpha Vision
                </h2>
                <p className="text-[11px] text-slate-500 font-semibold mt-0.5 flex items-center gap-1.5 truncate">
                    <Clock size={12} /> {lastUpdated || '等待同步'}
                </p>
            </div>

            <div className="flex flex-1 items-center justify-end gap-2">
                <button
                    onClick={onScan}
                    disabled={loading}
                    className="primary-button shrink-0"
                >
                    {loading ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                    {loading ? '分析中' : '扫描'}
                </button>

                <button
                    onClick={onOpenFilters}
                    className="toolbar-button shrink-0"
                    title="策略参数"
                >
                    <SlidersHorizontal size={17} />
                    参数
                </button>

                <div className="h-7 w-[1px] bg-slate-300 mx-1 hidden sm:block" />

                {/* Search Container */}
                <div className="relative w-full max-w-sm" ref={dropdownRef}>
                    <div className="relative group">
                        <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none text-slate-400 group-focus-within:text-blue-600 transition-colors">
                            {isSearching ? <Loader2 size={18} className="animate-spin text-blue-600" /> : <Search size={18} />}
                        </div>
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            onFocus={() => searchQuery.trim() && setShowDropdown(true)}
                            placeholder="代码/名称搜索..."
                            className="w-full pl-10 pr-8 py-2 bg-white/80 border border-slate-300 rounded-md text-sm font-semibold shadow-inner focus:ring-2 focus:ring-amber-400/20 focus:border-amber-300 transition-all outline-none"
                        />
                        {searchQuery && (
                            <button 
                                onClick={() => setSearchQuery('')}
                                className="absolute inset-y-0 right-2.5 flex items-center text-slate-400 hover:text-slate-600"
                            >
                                <X size={16} />
                            </button>
                        )}
                    </div>

                    {showDropdown && results.length > 0 && (
                        <div className="absolute right-0 mt-2 w-[min(24rem,calc(100vw-2rem))] bg-white border border-slate-200 rounded-lg shadow-xl max-h-96 overflow-y-auto z-50 p-2 space-y-1">
                            <div className="px-3 py-1.5 metric-label border-b border-slate-100">
                                股票检索结果 ({results.length})
                            </div>
                            {notice && (
                                <div className="mx-1 rounded-md bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700">
                                    {notice}
                                </div>
                            )}
                            {results.map((stock) => (
                                <div
                                    key={stock.code}
                                    className="flex items-center gap-2 rounded-md px-2 py-2 hover:bg-blue-50 transition-colors group"
                                >
                                    <button
                                        type="button"
                                        onClick={() => handleSelectStock(stock)}
                                        className="min-w-0 flex-1 text-left"
                                    >
                                        <p className="text-sm font-bold text-slate-800 group-hover:text-blue-700 transition-colors">
                                            {stock.name}
                                        </p>
                                        <p className="text-[10px] text-slate-400 font-bold tracking-wider">
                                            {stock.code} · {stock.industry}
                                        </p>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => addToWatchlist(stock)}
                                        disabled={addingWatchCode === stock.code}
                                        className="shrink-0 inline-flex items-center gap-1 rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] font-black text-amber-600 hover:bg-amber-100 disabled:opacity-60"
                                        title="加入观察池"
                                    >
                                        {addingWatchCode === stock.code ? <Loader2 size={15} className="animate-spin" /> : <Star size={15} />}
                                        观察
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleSelectStock(stock)}
                                        className="shrink-0 inline-flex items-center gap-1 rounded-lg bg-blue-50 px-2.5 py-2 text-[11px] font-black text-blue-600 hover:bg-blue-100"
                                        title="打开走势详情"
                                    >
                                        <ArrowRight size={15} />
                                        走势
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}

                    {showDropdown && searchQuery.trim() && results.length === 0 && !isSearching && (
                        <div className="absolute right-0 mt-2 w-80 bg-white border border-slate-200 rounded-lg shadow-xl z-50 p-6 text-center text-slate-400 text-sm">
                            未匹配到相关个股
                        </div>
                    )}
                </div>
            </div>
        </header>
    );
}
