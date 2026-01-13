"use client";

import React, { useState, useEffect } from 'react';
import { TrendingUp, Flame, List } from 'lucide-react';
import api from '@/lib/api';
import ThemeStocks from './ThemeStocks';

interface Theme {
    id: number;
    name: string;
    hotness: number;
    life_cycle_stage: string;
    leader_stock: string;
}

export default function MarketSentiment() {
    const [themes, setThemes] = useState<Theme[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedTheme, setSelectedTheme] = useState<Theme | null>(null);

    useEffect(() => {
        const fetchThemes = async () => {
            try {
                const res = await api.get('/api/news/themes?limit=10');
                setThemes(res.data.data || []);
            } catch (e) {
                console.error("Failed to fetch themes", e);
            } finally {
                setLoading(false);
            }
        };

        fetchThemes();
    }, []);

    if (loading) {
        return <div className="text-center text-slate-400">加载中...</div>;
    }

    if (themes.length === 0) {
        return null;
    }

    const getStageColor = (stage: string) => {
        switch (stage) {
            case 'emerging': return 'bg-red-500';
            case 'growing': return 'bg-orange-500';
            case 'mature': return 'bg-yellow-500';
            case 'declining': return 'bg-gray-500';
            default: return 'bg-gray-500';
        }
    };

    const getStageLabel = (stage: string) => {
        switch (stage) {
            case 'emerging': return '爆发期';
            case 'growing': return '成长期';
            case 'mature': return '成熟期';
            case 'declining': return '衰退期';
            default: return '未知';
        }
    };

    return (
        <div className="mb-6">
            <div className="flex items-center gap-2 mb-3">
                <Flame size={18} className="text-orange-500" />
                <h3 className="text-lg font-bold text-slate-700">热点题材</h3>
            </div>

            <div className="flex gap-4 overflow-x-auto pb-2 scrollbar-hide">
                {themes.slice(0, 10).map((theme) => (
                    <div
                        key={theme.id}
                        onClick={() => setSelectedTheme(theme)}
                        className="flex-shrink-0 bg-white border border-slate-200 rounded-2xl p-4 min-w-[200px] cursor-pointer hover:border-indigo-300 hover:shadow-lg transition-all"
                    >
                        <div className="flex items-center justify-between mb-2">
                            <span className="font-bold text-slate-800">{theme.name}</span>
                            <div className={`w-2 h-2 rounded-full ${getStageColor(theme.life_cycle_stage)}`} />
                        </div>
                        <div className="text-sm text-slate-500 mb-1">
                            热度: {theme.hotness.toFixed(0)}
                        </div>
                        <div className="text-xs text-slate-400 mb-2">
                            {getStageLabel(theme.life_cycle_stage)}
                        </div>
                        <div className="flex items-center justify-between">
                            {theme.leader_stock && (
                                <div className="text-xs font-mono text-indigo-600">
                                    龙头: {theme.leader_stock}
                                </div>
                            )}
                            <button className="text-xs text-indigo-500 hover:text-indigo-700 font-medium flex items-center gap-1">
                                <List size={12} />
                                成分股
                            </button>
                        </div>
                    </div>
                ))}
            </div>

            {selectedTheme && (
                <ThemeStocks
                    isOpen={!!selectedTheme}
                    onClose={() => setSelectedTheme(null)}
                    themeName={selectedTheme.name}
                    themeId={selectedTheme.id}
                />
            )}
        </div>
    );
}
