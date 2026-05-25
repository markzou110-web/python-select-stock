"use client";

import React, { useState, useEffect, useRef } from 'react';
import { Bell, Search, User, Play, Loader2, Clock, X, ArrowRight } from 'lucide-react';
import api from '@/lib/api';

interface HeaderProps {
    onScan: () => void;
    loading: boolean;
    lastUpdated: string;
    onOpenFilters: () => void;
    onSelectStock: (stock: any) => void;
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

    const handleSelectStock = async (stock: StockSearchResult) => {
        setShowDropdown(false);
        setSearchQuery('');
        
        // Fetch full stock details dynamically so AI Deep Dive has complete computed indicators
        try {
            const detailRes = await api.get(`/api/stock/detail?code=${stock.code}`);
            if (detailRes.data && detailRes.data.stock_info) {
                // Pass full enriched stock object to dashboard selectedStock
                onSelectStock(detailRes.data.stock_info);
            } else {
                // Fallback to basic object if endpoint returns nothing
                onSelectStock({
                    代码: stock.code,
                    名称: stock.name,
                    行业: stock.industry,
                    现价: 0
                });
            }
        } catch (err) {
            console.error("Error fetching selected stock details:", err);
            onSelectStock({
                代码: stock.code,
                名称: stock.name,
                行业: stock.industry,
                现价: 0
            });
        }
    };

    return (
        <header className="flex items-center justify-between px-8 py-6">
            <div>
                <h2 className="text-2xl font-extrabold text-slate-800 tracking-tight flex items-center gap-2">
                    Alpha Vision <span className="text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded text-xs font-bold">PRO</span>
                </h2>
                <p className="text-sm text-slate-400 font-medium mt-1 flex items-center gap-2">
                    <Clock size={12} /> 最后同步: {lastUpdated}
                </p>
            </div>

            <div className="flex items-center gap-4">
                <button
                    onClick={onScan}
                    disabled={loading}
                    className="flex items-center gap-2 px-6 py-2.5 premium-gradient text-white rounded-xl font-bold shadow-lg shadow-indigo-100 transition-all hover:scale-105 active:scale-95 disabled:opacity-50"
                >
                    {loading ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                    {loading ? '正在分析...' : '一键扫描'}
                </button>

                <div className="h-8 w-[1px] bg-slate-200 mx-2" />

                {/* Search Container */}
                <div className="relative" ref={dropdownRef}>
                    <div className="relative group">
                        <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none text-slate-400 group-focus-within:text-indigo-500 transition-colors">
                            {isSearching ? <Loader2 size={18} className="animate-spin text-indigo-500" /> : <Search size={18} />}
                        </div>
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            onFocus={() => searchQuery.trim() && setShowDropdown(true)}
                            placeholder="代码/名称搜索..."
                            className="pl-10 pr-8 py-2 bg-slate-100 border-none rounded-xl text-sm font-medium w-64 focus:ring-2 focus:ring-indigo-500 transition-all outline-none"
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

                    {/* Glassmorphism Results Dropdown */}
                    {showDropdown && results.length > 0 && (
                        <div className="absolute right-0 mt-2 w-72 bg-white/90 backdrop-blur-xl border border-slate-200/50 rounded-2xl shadow-2xl max-h-80 overflow-y-auto z-50 p-2 space-y-1">
                            <div className="px-3 py-1.5 text-[10px] font-black text-slate-400 tracking-wider uppercase border-b border-slate-100">
                                股票检索结果 ({results.length})
                            </div>
                            {results.map((stock) => (
                                <div
                                    key={stock.code}
                                    onClick={() => handleSelectStock(stock)}
                                    className="flex items-center justify-between px-3 py-2.5 hover:bg-indigo-50/50 rounded-xl cursor-pointer transition-all group"
                                >
                                    <div>
                                        <p className="text-sm font-bold text-slate-800 group-hover:text-indigo-600 transition-all">
                                            {stock.name}
                                        </p>
                                        <p className="text-[10px] text-slate-400 font-bold tracking-wider">
                                            {stock.code} · {stock.industry}
                                        </p>
                                    </div>
                                    <ArrowRight size={14} className="text-slate-300 opacity-0 group-hover:opacity-100 group-hover:text-indigo-500 group-hover:translate-x-0.5 transition-all" />
                                </div>
                            ))}
                        </div>
                    )}

                    {showDropdown && searchQuery.trim() && results.length === 0 && !isSearching && (
                        <div className="absolute right-0 mt-2 w-72 bg-white/90 backdrop-blur-xl border border-slate-200/50 rounded-2xl shadow-2xl z-50 p-6 text-center text-slate-400 text-sm">
                            未匹配到相关个股
                        </div>
                    )}
                </div>

                <div className="w-10 h-10 bg-slate-100 rounded-full flex items-center justify-center text-slate-500 cursor-pointer hover:bg-indigo-50 hover:text-indigo-600 transition-all">
                    <User size={20} />
                </div>
            </div>
        </header>
    );
}
