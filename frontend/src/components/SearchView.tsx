"use client";

import React, { useState } from 'react';
import { ArrowRight, Loader2, Search, Star } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';
import StockDetailPage from './StockDetailPage';

interface StockSearchResult {
    code: string;
    name: string;
    industry: string;
}

export default function SearchView() {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<StockSearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [selected, setSelected] = useState<StockSearchResult | null>(null);
    const [feedback, setFeedback] = useState('');
    const [searchFailed, setSearchFailed] = useState(false);

    const search = async () => {
        if (!query.trim()) {
            setFeedback('请输入股票代码、名称或拼音首字母。');
            return;
        }
        setLoading(true);
        setFeedback('');
        setSearchFailed(false);
        try {
            const res = await api.get(`/api/stock/search?query=${encodeURIComponent(query.trim())}`);
            setResults(res.data || []);
            setFeedback(`找到 ${(res.data || []).length} 只股票。`);
        } catch {
            setResults([]);
            setSearchFailed(true);
            setFeedback('无法完成搜索，请检查连接后重试。');
        } finally {
            setLoading(false);
        }
    };

    const addToWatchlist = async (stock: StockSearchResult) => {
        const detail = await api.get(`/api/stock/detail?code=${stock.code}`);
        const info = detail.data?.stock_info || {};
        await api.post('/api/watchlist/add', {
            code: stock.code,
            name: stock.name,
            industry: stock.industry,
            watch_price: info.现价 || 1,
            strategy_type: info.strategy_type || 'squeeze',
            reason: '代码检索加入观察池',
            source: 'search'
        });
        setFeedback(`${stock.name} 已加入观察池。`);
    };

    if (selected) {
        return <StockDetailPage code={selected.code} name={selected.name} onBack={() => setSelected(null)} />;
    }

    return (
        <div className="mx-auto max-w-5xl space-y-6">
            <div>
                <p className="text-xs font-semibold tracking-wide text-blue-700">快速个股入口</p>
                <h2 className="page-heading mt-1">搜索并打开个股分析</h2>
                <p className="page-description">输入代码、名称或拼音首字母，直接查看走势证据或加入观察池。</p>
            </div>

            <form
                className="glass-card flex flex-col gap-3 p-4 sm:flex-row sm:p-5"
                onSubmit={(event) => {
                    event.preventDefault();
                    void search();
                }}
            >
                <div className="relative flex-1">
                    <Search size={18} className="absolute start-4 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <label htmlFor="stock-lookup-query" className="sr-only">股票代码、名称或拼音首字母</label>
                    <input
                        id="stock-lookup-query"
                        name="stock-lookup-query"
                        type="search"
                        autoComplete="off"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        placeholder="例如：600519、贵州茅台、GZMT"
                        aria-describedby="stock-lookup-hint"
                        className="min-h-12 w-full rounded-xl border border-slate-200 bg-slate-50 py-3 ps-11 pe-4 text-base text-slate-800 transition-[border-color,box-shadow,background-color] placeholder:text-slate-400 focus:border-blue-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-blue-500/10 sm:text-sm"
                    />
                    <p id="stock-lookup-hint" className="sr-only">支持股票代码、中文名称和拼音首字母。</p>
                </div>
                <button type="submit" disabled={loading} className="primary-button min-h-12 px-6">
                    {loading ? <Loader2 size={17} className="animate-spin" aria-hidden="true" /> : <Search size={17} aria-hidden="true" />}
                    {loading ? '正在搜索…' : '搜索股票'}
                </button>
            </form>

            <p className={cn(
                "min-h-5 text-sm",
                searchFailed ? "text-rose-700" : feedback ? "text-slate-600" : "sr-only"
            )} role={searchFailed ? 'alert' : 'status'} aria-live="polite">{feedback}</p>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {results.map(stock => (
                    <article key={stock.code} className="glass-card flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0">
                            <h3 className="truncate font-semibold text-slate-900">{stock.name}</h3>
                            <p className="mt-1 truncate text-xs text-slate-500"><span className="font-mono tabular-nums">{stock.code}</span> · {stock.industry}</p>
                        </div>
                        <div className="flex gap-2">
                            <button type="button" onClick={() => void addToWatchlist(stock)} className="toolbar-button flex-1 sm:flex-none">
                                <Star size={17} aria-hidden="true" />
                                加入观察
                            </button>
                            <button type="button" onClick={() => setSelected(stock)} className="primary-button flex-1 sm:flex-none">
                                打开分析
                                <ArrowRight size={17} aria-hidden="true" />
                            </button>
                        </div>
                    </article>
                ))}
            </div>

            {!loading && query && results.length === 0 && (
                <div className="glass-card p-10 text-center sm:p-14">
                    <p className="font-semibold text-slate-800">{searchFailed ? '搜索暂不可用' : `未找到“${query.trim()}”`}</p>
                    <p className="mt-1 text-sm text-slate-500">{searchFailed ? '请检查服务连接后重试。' : '请检查股票代码、名称或拼音后重试。'}</p>
                    <button type="button" onClick={() => setQuery('')} className="toolbar-button mt-4">清空搜索</button>
                </div>
            )}
        </div>
    );
}
