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
                <div className="workspace-panel flex min-h-64 flex-col items-center justify-center gap-4 border-rose-200 bg-rose-50/60 p-8 text-center" role="alert">
                    <div className="flex size-12 items-center justify-center rounded-xl bg-rose-100 text-rose-700">
                        <AlertTriangle size={24} aria-hidden="true" />
                    </div>
                    <div>
                        <h2 className="text-base font-semibold text-rose-900">{this.props.fallbackTitle || '模块加载异常'}</h2>
                        <p className="mt-1 max-w-md text-sm text-rose-700">
                            当前模块未能完成加载。请重试；如果问题持续出现，请检查服务连接。
                        </p>
                        <p className="mt-2 max-w-md break-words font-mono text-xs text-rose-600">
                            {this.state.error?.message || '未知错误'}
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => this.setState({ hasError: false, error: null })}
                        className="toolbar-button border-rose-200 text-rose-800 hover:border-rose-300 hover:bg-rose-100 hover:text-rose-900"
                    >
                        <RefreshCw size={15} aria-hidden="true" />
                        重新加载模块
                    </button>
                </div>
            );
        }
        return this.props.children;
    }
}
