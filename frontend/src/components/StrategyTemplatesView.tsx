"use client";

import React, { useEffect, useState } from 'react';
import { CheckCircle2, Loader2, Save, SlidersHorizontal, Trash2 } from 'lucide-react';
import api from '@/lib/api';
import { useScanStore } from '@/stores/scanStore';
import { cn } from '@/lib/utils';

export default function StrategyTemplatesView() {
    const params = useScanStore(s => s.params);
    const setParams = useScanStore(s => s.setParams);
    const [templates, setTemplates] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [appliedId, setAppliedId] = useState<number | null>(null);

    const fetchTemplates = async () => {
        setLoading(true);
        try {
            const res = await api.get('/api/strategy-templates/list');
            setTemplates(res.data.templates || []);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchTemplates(); }, []);

    const saveTemplate = async () => {
        if (!name.trim()) return;
        await api.post('/api/strategy-templates/save', {
            name,
            description,
            strategy_type: params.strategy_type,
            params,
        });
        setName('');
        setDescription('');
        fetchTemplates();
    };

    const applyTemplate = (tpl: any) => {
        setParams({ ...params, ...tpl.params });
        setAppliedId(tpl.id);
        setTimeout(() => setAppliedId(null), 2500);
    };

    const removeTemplate = async (id: number) => {
        if (!confirm("确定删除该策略模板吗？")) return;
        await api.delete(`/api/strategy-templates/remove/${id}`);
        fetchTemplates();
    };

    return (
        <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div>
                <h2 className="text-2xl font-black text-slate-900">策略模板</h2>
                <p className="text-xs text-slate-400 font-bold uppercase tracking-widest mt-1">Reusable scan parameter presets</p>
            </div>

            <div className="glass-card p-6 grid grid-cols-1 md:grid-cols-[1fr_2fr_auto] gap-3 items-end">
                <div>
                    <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest block mb-1.5">模板名称</label>
                    <input value={name} onChange={e => setName(e.target.value)} placeholder="例如：低位强动能" className="w-full px-4 py-3 bg-slate-50 border border-slate-100 rounded-2xl text-sm font-bold outline-none focus:ring-4 focus:ring-indigo-500/10" />
                </div>
                <div>
                    <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest block mb-1.5">说明</label>
                    <input value={description} onChange={e => setDescription(e.target.value)} placeholder="记录这套参数适用的行情和风险偏好" className="w-full px-4 py-3 bg-slate-50 border border-slate-100 rounded-2xl text-sm font-bold outline-none focus:ring-4 focus:ring-indigo-500/10" />
                </div>
                <button onClick={saveTemplate} className="px-5 py-3 rounded-2xl bg-indigo-600 text-white text-sm font-black flex items-center gap-2">
                    <Save size={16} />
                    保存当前参数
                </button>
            </div>

            {loading ? (
                <div className="p-20 text-center text-slate-400"><Loader2 className="animate-spin inline mr-2" /> 正在加载策略模板...</div>
            ) : (
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                    {templates.map(tpl => (
                        <div key={tpl.id} className="glass-card p-6 space-y-5">
                            <div className="flex items-start justify-between">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <h3 className="font-black text-slate-800">{tpl.name}</h3>
                                        {tpl.is_default && <span className="text-[9px] font-black px-2 py-0.5 bg-indigo-50 text-indigo-600 rounded-lg">内置</span>}
                                    </div>
                                    <p className="text-xs font-bold text-slate-400 mt-1">{tpl.description || "无说明"}</p>
                                </div>
                                <SlidersHorizontal size={18} className="text-indigo-400" />
                            </div>
                            <div className="grid grid-cols-2 gap-2 text-[10px] font-bold text-slate-500">
                                <Param label="策略" value={tpl.params.strategy_type || tpl.strategy_type} />
                                <Param label="RSI" value={tpl.params.rsi_min} />
                                <Param label="量比" value={tpl.params.vol_multiplier} />
                                <Param label="止损" value={`${tpl.params.stop_loss_pct}%`} />
                            </div>
                            <div className="flex justify-between gap-2">
                                <button onClick={() => applyTemplate(tpl)} className={cn("flex-1 py-2 rounded-xl text-xs font-black flex items-center justify-center gap-1.5", appliedId === tpl.id ? "bg-emerald-500 text-white" : "bg-indigo-50 text-indigo-600 hover:bg-indigo-100")}>
                                    {appliedId === tpl.id ? <CheckCircle2 size={14} /> : <SlidersHorizontal size={14} />}
                                    {appliedId === tpl.id ? "已应用" : "应用参数"}
                                </button>
                                {!tpl.is_default && (
                                    <button onClick={() => removeTemplate(tpl.id)} className="p-2 rounded-xl bg-slate-50 text-slate-400 hover:bg-rose-50 hover:text-rose-600">
                                        <Trash2 size={15} />
                                    </button>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

function Param({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-100">
            <span className="text-slate-400">{label}</span>
            <span className="float-right text-slate-700">{value}</span>
        </div>
    );
}
