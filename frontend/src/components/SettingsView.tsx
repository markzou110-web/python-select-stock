"use client";

import React, { useState, useEffect } from 'react';
import {
    Clock,
    Bell,
    Save,
    Settings as SettingsIcon,
    ShieldCheck,
    Cpu,
    Database,
    CheckCircle2,
    Loader2
} from 'lucide-react';
import { cn } from '@/lib/utils';
import api from '@/lib/api';

export default function SettingsView() {
    const [settings, setSettings] = useState({
        sentinel_schedule_times: "14:20",
        bark_key: ""
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        fetchSettings();
    }, []);

    const fetchSettings = async () => {
        try {
            const res = await api.get('/api/settings');
            setSettings(res.data);
        } catch (err) {
            console.error("Fetch Settings Error:", err);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.post('/api/settings', settings);
            setSaved(true);
            setTimeout(() => setSaved(false), 3000);
        } catch (err) {
            console.error("Save Settings Error:", err);
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="flex-1 flex items-center justify-center p-20 text-slate-400">
                <Loader2 className="animate-spin mr-2" /> 正在加载系统配置...
            </div>
        );
    }

    return (
        <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-indigo-600 text-white rounded-2xl flex items-center justify-center shadow-lg shadow-indigo-100">
                        <SettingsIcon size={24} />
                    </div>
                    <div>
                        <h2 className="text-2xl font-black text-slate-900">系统配置</h2>
                        <p className="text-slate-400 font-bold text-xs uppercase tracking-widest mt-1">System Terminal Configuration</p>
                    </div>
                </div>

                <button
                    onClick={handleSave}
                    disabled={saving}
                    className={cn(
                        "flex items-center gap-2 px-6 py-3 rounded-2xl font-bold text-sm transition-all shadow-xl active:scale-95",
                        saved
                            ? "bg-emerald-500 text-white shadow-emerald-100"
                            : "bg-indigo-600 text-white hover:bg-indigo-700 shadow-indigo-100"
                    )}
                >
                    {saving ? <Loader2 size={18} className="animate-spin" /> : (saved ? <CheckCircle2 size={18} /> : <Save size={18} />)}
                    {saved ? "设置已保存" : "保存全局设置"}
                </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                {/* Automation Section */}
                <div className="space-y-6">
                    <div className="flex items-center gap-2 px-2 text-slate-400">
                        <Cpu size={18} />
                        <h3 className="font-black text-[10px] uppercase tracking-widest">自动化工作流</h3>
                    </div>

                    <div className="glass-card p-6 space-y-6">
                        <div className="space-y-3">
                            <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <Clock size={16} className="text-indigo-500" />
                                哨兵自动巡检时间点
                            </label>
                            <input
                                type="text"
                                value={settings.sentinel_schedule_times || ""}
                                onChange={(e) => setSettings({ ...settings, sentinel_schedule_times: e.target.value })}
                                placeholder="例如: 10:30, 14:20, 14:50"
                                className="w-full bg-slate-50 border border-slate-100 text-slate-600 font-mono font-bold rounded-xl px-4 py-3 outline-none focus:ring-4 focus:ring-indigo-500/10 focus:bg-white transition-all"
                            />
                            <p className="text-[10px] text-slate-400 font-medium leading-relaxed">
                                💡 支持配置多个时间点（用英文逗号分隔，如 <code className="bg-slate-100 px-1 py-0.5 rounded">14:20, 14:50</code>）。系统将在每日配置时刻自动获取最新行情、分析全市场标的，并触发推送通知。
                            </p>
                        </div>

                        <div className="p-4 bg-amber-50 rounded-2xl border border-amber-100/50 flex gap-3">
                            <span className="text-xl">⚠️</span>
                            <div className="text-[10px] text-amber-700 font-medium leading-tight">
                                提示：请确保程序在此时间段处于运行状态。建议设置在下午 14:00 - 15:00 之间，以获得最准的确诊信号。
                            </div>
                        </div>
                    </div>
                </div>

                {/* Notifications Section */}
                <div className="space-y-6">
                    <div className="flex items-center gap-2 px-2 text-slate-400">
                        <Bell size={18} />
                        <h3 className="font-black text-[10px] uppercase tracking-widest">消息通知服务</h3>
                    </div>

                    <div className="glass-card p-6 space-y-6">
                        <div className="space-y-3">
                            <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <ShieldCheck size={16} className="text-indigo-500" />
                                Bark 推送 Key (当前库)
                            </label>
                            <input
                                type="text"
                                value={settings.bark_key || ""}
                                onChange={(e) => setSettings({ ...settings, bark_key: e.target.value })}
                                placeholder="输入您的 Bark Key"
                                className="w-full bg-slate-50 border border-slate-100 text-slate-600 font-mono font-bold rounded-xl px-4 py-3 outline-none focus:ring-4 focus:ring-indigo-500/10 focus:bg-white transition-all"
                            />
                            <p className="text-[10px] text-slate-400 font-medium">
                                🔒 保存后将优先使用此 Key。如果为空，则使用 .env 文件中的默认配置。
                            </p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Performance Hints */}
            <div className="glass-card p-8 bg-slate-900 text-slate-300 relative overflow-hidden">
                <div className="relative z-10 flex gap-6">
                    <div className="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center shrink-0">
                        <Database size={32} className="text-indigo-400" />
                    </div>
                    <div className="space-y-2">
                        <h4 className="text-white font-bold">后台算力状态</h4>
                        <p className="text-xs font-medium text-slate-400 leading-relaxed">
                            自动化扫描将使用 <span className="text-indigo-400">本地极速模式</span> 进行。
                            单次全市场筛选耗时约为 <span className="text-white">5-10 秒</span>。
                            所有筛选结果将自动存入 `scan_history` 表供回顾。
                        </p>
                    </div>
                </div>
                <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/10 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2" />
            </div>
        </div>
    );
}
