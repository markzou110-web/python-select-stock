"use client";

import React, { useState } from 'react';
import { ArrowRight, Loader2, Search, Star } from 'lucide-react';
import api from '@/lib/api';
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

    const search = async () => {
        if (!query.trim()) return;
        setLoading(true);
        try {
            const res = await api.get(`/api/stock/search?query=${encodeURIComponent(query.trim())}`);
            setResults(res.data || []);
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
    };

    if (selected) {
        return <StockDetailPage code={selected.code} name={selected.name} onBack={() => setSelected(null)} />;
    }

    return (
        <div className="max-w-5xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div>
                <h2 className="text-2xl font-black text-slate-900">代码检索</h2>
                <p className="text-xs text-slate-400 font-bold uppercase tracking-widest mt-1">Stock lookup and quick analysis</p>
            </div>

            <div className="glass-card p-6 flex gap-3">
                <div className="relative flex-1">
                    <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') search(); }}
                        placeholder="输入股票代码、名称或拼音首字母"
                        className="w-full pl-11 pr-4 py-3 bg-slate-50 border border-slate-100 rounded-2xl outline-none focus:ring-4 focus:ring-indigo-500/10 focus:bg-white text-sm font-bold text-slate-700"
                    />
                </div>
                <button onClick={search} disabled={loading} className="px-6 py-3 rounded-2xl bg-indigo-600 text-white font-black text-sm flex items-center gap-2 disabled:opacity-50">
                    {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                    检索
                </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {results.map(stock => (
                    <div key={stock.code} className="glass-card p-5 flex items-center justify-between">
                        <div>
                            <h3 className="font-black text-slate-800">{stock.name}</h3>
                            <p className="text-xs font-mono font-bold text-slate-400 mt-1">{stock.code} · {stock.industry}</p>
                        </div>
                        <div className="flex gap-2">
                            <button onClick={() => addToWatchlist(stock)} className="p-2 rounded-xl bg-amber-50 text-amber-600 hover:bg-amber-100 transition-all" title="加入观察池">
                                <Star size={17} />
                            </button>
                            <button onClick={() => setSelected(stock)} className="p-2 rounded-xl bg-indigo-50 text-indigo-600 hover:bg-indigo-100 transition-all" title="深度分析">
                                <ArrowRight size={17} />
                            </button>
                        </div>
                    </div>
                ))}
            </div>

            {!loading && query && results.length === 0 && (
                <div className="glass-card p-16 text-center text-slate-400 font-bold">暂无匹配结果</div>
            )}
        </div>
    );
}
