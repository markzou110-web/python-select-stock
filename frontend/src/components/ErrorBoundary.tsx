"use client";

import React, { Component, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface Props {
    children: ReactNode;
    fallbackTitle?: string;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error('[ErrorBoundary]', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex flex-col items-center justify-center p-10 bg-rose-50/50 border border-rose-100 rounded-2xl text-center gap-4">
                    <div className="w-12 h-12 bg-rose-100 text-rose-500 rounded-xl flex items-center justify-center">
                        <AlertTriangle size={24} />
                    </div>
                    <div>
                        <p className="font-bold text-rose-700 text-sm">{this.props.fallbackTitle || '模块加载异常'}</p>
                        <p className="text-[10px] text-rose-400 mt-1 font-mono max-w-xs truncate">
                            {this.state.error?.message || '未知错误'}
                        </p>
                    </div>
                    <button
                        onClick={() => this.setState({ hasError: false, error: null })}
                        className="flex items-center gap-1.5 px-4 py-2 text-xs font-bold text-rose-600 bg-white border border-rose-200 rounded-xl hover:bg-rose-50 transition-all"
                    >
                        <RefreshCw size={12} />
                        重试
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}
