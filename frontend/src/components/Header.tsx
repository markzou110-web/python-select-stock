"use client";

import React, { useState, useEffect, useRef } from 'react';
import { Search, Play, Loader2, Clock, X, ArrowRight, Star, SlidersHorizontal, Menu } from 'lucide-react';
import api from '@/lib/api';

interface HeaderProps {
    onScan: () => void;
    loading: boolean;
    lastUpdated: string;
    onOpenFilters: () => void;
    onSelectStock: (stock: StockSearchResult) => void;
    activeView: string;
    onOpenNavigation: () => void;
}

interface StockSearchResult {
    code: string;
    name: string;
    industry: string;
}

const VIEW_META: Record<string, { title: string; description: string }> = {
    overview: { title: '系统概览', description: '市场、策略与执行状态摘要' },
    scanner: { title: '多因子共振', description: '全市场扫描与候选股决策' },
    'sector-radar': { title: '板块雷达', description: '识别主线方向与板块强度' },
    'research-radar': { title: '资讯雷达', description: '聚合题材信息与个股线索' },
    themes: { title: '题材热点', description: '题材热度、资金与新闻证据' },
    paper: { title: '拟合实盘', description: '验证仓位、收益与风险约束' },
    watchlist: { title: '观察池', description: '跟踪候选股与触发条件' },
    review: { title: '交易复盘', description: '评估信号质量与执行结果' },
    'execution-inbox': { title: '执行收件箱', description: '处理待确认的交易意图' },
    backtest: { title: '策略回测', description: '验证策略参数与历史表现' },
    alerts: { title: '实时告警', description: '处理止损、破位与止盈提醒' },
    ops: { title: '专业驾驶舱', description: '监控任务、数据与策略健康度' },
    search: { title: '代码检索', description: '快速定位个股并进入分析' },
    templates: { title: '策略模板', description: '管理常用扫描参数组合' },
    settings: { title: '系统配置', description: '管理数据源与通知偏好' },
};

export default function Header({
    onScan,
    loading,
    lastUpdated,
    onOpenFilters,
    onSelectStock,
    activeView,
    onOpenNavigation,
}: HeaderProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [results, setResults] = useState<StockSearchResult[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const [showDropdown, setShowDropdown] = useState(false);
    const [addingWatchCode, setAddingWatchCode] = useState<string | null>(null);
    const [notice, setNotice] = useState<string>('');
    const [searchError, setSearchError] = useState('');
    const dropdownRef = useRef<HTMLDivElement>(null);
    const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const viewMeta = VIEW_META[activeView] || VIEW_META.overview;

    // Close dropdown on click outside
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setShowDropdown(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        const handleEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') setShowDropdown(false);
        };
        document.addEventListener('keydown', handleEscape);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
            document.removeEventListener('keydown', handleEscape);
        };
    }, []);

    // Fetch stock search results on query change
    useEffect(() => {
        if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
        }

        if (!searchQuery.trim()) {
            setResults([]);
            setIsSearching(false);
            setShowDropdown(false);
            setSearchError('');
            return;
        }

        setIsSearching(true);
        setSearchError('');
        searchTimeoutRef.current = setTimeout(async () => {
            try {
                const res = await api.get(`/api/stock/search?query=${encodeURIComponent(searchQuery.trim())}`);
                setResults(res.data || []);
                setShowDropdown(true);
            } catch (err) {
                console.error("Fuzzy Search Error:", err);
                setResults([]);
                setSearchError('无法搜索，请检查连接后重试。');
                setShowDropdown(true);
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
        <header className="sticky top-0 z-30 bg-[color:oklch(0.99_0.004_90/0.94)] px-3 py-3 backdrop-blur-xl after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-gradient-to-r after:from-transparent after:via-slate-200/50 after:to-transparent sm:px-5">
            <div className="mx-auto flex w-full max-w-[1920px] flex-wrap items-center gap-3">
                <div className="flex min-w-0 flex-1 items-center gap-3">
                    <button
                        type="button"
                        onClick={onOpenNavigation}
                        className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 md:hidden"
                        aria-label="打开主导航"
                        aria-controls="app-navigation"
                    >
                        <Menu size={20} aria-hidden="true" />
                    </button>
                    <div className="min-w-0">
                        <h1 className="truncate text-lg font-bold tracking-[0.01em] text-slate-950 sm:text-xl">
                            {viewMeta.title}
                        </h1>
                        <p className="hidden truncate text-xs text-slate-500 sm:block">{viewMeta.description}</p>
                    </div>
                </div>

                <div className="order-3 w-full lg:order-none lg:w-auto lg:flex-1">
                    {/* Search Container */}
                    <div className="relative ms-auto w-full lg:max-w-sm" ref={dropdownRef}>
                    <div className="relative group">
                        <div className="pointer-events-none absolute inset-y-0 start-3 flex items-center text-slate-400 transition-colors group-focus-within:text-blue-600">
                            {isSearching ? <Loader2 size={18} className="animate-spin text-blue-600" aria-hidden="true" /> : <Search size={18} aria-hidden="true" />}
                        </div>
                        <label htmlFor="global-stock-search" className="sr-only">搜索股票代码或名称</label>
                        <input
                            id="global-stock-search"
                            type="search"
                            name="stock-search"
                            autoComplete="off"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            onFocus={() => searchQuery.trim() && setShowDropdown(true)}
                            placeholder="搜索代码、名称或拼音"
                            role="combobox"
                            aria-expanded={showDropdown}
                            aria-controls="header-search-results"
                            aria-autocomplete="list"
                            className="min-h-10 w-full rounded-xl border border-slate-200 bg-white py-2 ps-10 pe-11 text-base text-slate-800 shadow-sm transition-[border-color,box-shadow] placeholder:font-normal placeholder:text-slate-400 focus:border-blue-400 focus:outline-none focus:ring-4 focus:ring-blue-500/10 sm:text-sm"
                        />
                        {searchQuery && (
                            <button 
                                type="button"
                                onClick={() => setSearchQuery('')}
                                className="absolute inset-y-0 end-1 inline-flex w-10 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                aria-label="清空股票搜索"
                            >
                                <X size={16} aria-hidden="true" />
                            </button>
                        )}
                    </div>

                        {showDropdown && !isSearching && (
                            <div
                                id="header-search-results"
                                className="absolute end-0 z-50 mt-2 max-h-96 w-full min-w-0 space-y-1 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-2 shadow-[var(--shadow-raised)] lg:w-[26rem]"
                                aria-label="股票搜索结果"
                            >
                                {results.length > 0 ? (
                                    <>
                                        <div className="flex items-center justify-between px-3 py-2">
                                            <p className="text-xs font-semibold text-slate-500">找到 {results.length} 只股票</p>
                                            <p className="text-xs text-slate-400">选择股票进入详情</p>
                                        </div>
                                        {notice && (
                                            <div className="mx-1 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800">
                                                {notice}
                                            </div>
                                        )}
                                        {results.map((stock) => (
                                            <div
                                                key={stock.code}
                                                className="group flex items-center gap-2 rounded-xl px-2 py-2 hover:bg-blue-50"
                                            >
                                                <button
                                                    type="button"
                                                    onClick={() => handleSelectStock(stock)}
                                                    className="min-h-10 min-w-0 flex-1 rounded-lg px-1 text-start"
                                                >
                                                    <p className="text-sm font-semibold text-slate-800 transition-colors group-hover:text-blue-700">
                                                        {stock.name}
                                                    </p>
                                                    <p className="mt-0.5 truncate text-xs text-slate-500">
                                                        <span className="font-mono tabular-nums">{stock.code}</span> · {stock.industry}
                                                    </p>
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => addToWatchlist(stock)}
                                                    disabled={addingWatchCode === stock.code}
                                                    className="inline-flex min-h-10 shrink-0 items-center gap-1 rounded-lg bg-amber-50 px-2.5 text-xs font-semibold text-amber-800 hover:bg-amber-100 disabled:opacity-60"
                                                >
                                                    {addingWatchCode === stock.code ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Star size={15} aria-hidden="true" />}
                                                    观察
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => handleSelectStock(stock)}
                                                    className="inline-flex min-h-10 shrink-0 items-center gap-1 rounded-lg bg-blue-50 px-2.5 text-xs font-semibold text-blue-700 hover:bg-blue-100"
                                                >
                                                    <ArrowRight size={15} aria-hidden="true" />
                                                    详情
                                                </button>
                                            </div>
                                        ))}
                                    </>
                                ) : (
                                    <div className="px-4 py-6 text-center">
                                        <p className="font-semibold text-slate-700">
                                            {searchError ? '搜索暂不可用' : `未找到“${searchQuery.trim()}”`}
                                        </p>
                                        <p className="mt-1 text-sm text-slate-500">
                                            {searchError || '请检查代码、名称或拼音后重试。'}
                                        </p>
                                    </div>
                                )}
                            </div>
                        )}
                        <p className="sr-only" role="status" aria-live="polite">
                            {searchError || notice || (showDropdown && !isSearching ? `找到 ${results.length} 只股票` : '')}
                        </p>
                    </div>
                </div>

                <div className="flex shrink-0 items-center gap-2">
                    <div className="me-1 hidden text-end xl:block">
                        <p className="flex items-center justify-end gap-1.5 text-xs font-medium text-slate-500">
                            <Clock size={13} aria-hidden="true" />
                            数据更新
                        </p>
                        <p className="mt-0.5 text-xs tabular-nums text-slate-700">{lastUpdated || '等待同步'}</p>
                    </div>
                    <button
                        type="button"
                        onClick={onOpenFilters}
                        className="toolbar-button shrink-0"
                    >
                        <SlidersHorizontal size={17} aria-hidden="true" />
                        <span className="hidden sm:inline">策略参数</span>
                        <span className="sm:hidden">参数</span>
                    </button>
                    <button
                        type="button"
                        onClick={onScan}
                        disabled={loading}
                        className="primary-button shrink-0"
                    >
                        {loading ? <Loader2 size={18} className="animate-spin" aria-hidden="true" /> : <Play size={18} fill="currentColor" aria-hidden="true" />}
                        {loading ? '正在扫描…' : '开始扫描'}
                    </button>
                </div>
            </div>
        </header>
    );
}
