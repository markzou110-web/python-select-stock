"use client";

import React, { useState, useEffect } from 'react';
import { Bell, BellRing, X, ExternalLink, Clock } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Notification {
    id: string;
    type: 'news' | 'risk' | 'theme';
    title: string;
    message: string;
    url?: string;
    timestamp: Date;
    read: boolean;
}

export default function NewsNotification() {
    const [notifications, setNotifications] = useState<Notification[]>([]);
    const [isOpen, setIsOpen] = useState(false);
    const [permission, setPermission] = useState<NotificationPermission>('default');

    useEffect(() => {
        // 请求通知权限
        if ('Notification' in window) {
            setPermission(Notification.permission);
            if (Notification.permission === 'default') {
                Notification.requestPermission().then(setPermission);
            }
        }

        // 模拟获取通知
        const mockNotifications: Notification[] = [
            {
                id: '1',
                type: 'risk',
                title: '风险预警',
                message: '600519 (贵州茅台) 检测到财务高风险',
                timestamp: new Date(Date.now() - 5 * 60 * 1000),
                read: false,
                url: 'http://127.0.0.1:8000/api/news/risks?days=7'
            },
            {
                id: '2',
                type: 'theme',
                title: '题材异动',
                message: '人工智能热度上升至 150',
                timestamp: new Date(Date.now() - 15 * 60 * 1000),
                read: false,
                url: 'http://127.0.0.1:8000/api/news/themes?limit=10'
            }
        ];
        setNotifications(mockNotifications);

        // 设置定时轮询（每分钟）
        const interval = setInterval(() => {
            // TODO: 调用 API 获取最新通知
            checkNewNotifications();
        }, 60000);

        return () => clearInterval(interval);
    }, []);

    const checkNewNotifications = async () => {
        // TODO: 实现轮询逻辑
        // const res = await api.get('/api/notifications');
        // setNotifications(res.data);
    };

    const markAsRead = (id: string) => {
        setNotifications(prev =>
            prev.map(n => n.id === id ? { ...n, read: true } : n)
        );
    };

    const markAllAsRead = () => {
        setNotifications(prev =>
            prev.map(n => ({ ...n, read: true }))
        );
    };

    const unreadCount = notifications.filter(n => !n.read).length;

    const sendBrowserNotification = (notification: Notification) => {
        if (permission === 'granted' && !notification.read) {
            new Notification(notification.title, {
                body: notification.message,
                icon: '/favicon.ico',
                tag: notification.id,
                timestamp: notification.timestamp.getTime()
            });
        }
    };

    // 发送未读通知
    useEffect(() => {
        notifications.forEach(n => {
            if (!n.read) {
                sendBrowserNotification(n);
            }
        });
    }, [notifications, permission]);

    const getNotificationIcon = (type: string) => {
        switch (type) {
            case 'risk':
                return '⚠️';
            case 'theme':
                return '🔥';
            case 'news':
                return '📰';
            default:
                return '📢';
        }
    };

    const getNotificationColor = (type: string) => {
        switch (type) {
            case 'risk':
                return 'border-red-200 bg-red-50 hover:bg-red-100';
            case 'theme':
                return 'border-orange-200 bg-orange-50 hover:bg-orange-100';
            case 'news':
                return 'border-blue-200 bg-blue-50 hover:bg-blue-100';
            default:
                return 'border-slate-200 bg-slate-50 hover:bg-slate-100';
        }
    };

    return (
        <div className="relative">
            {/* Bell Button */}
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="relative p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all"
                title="通知"
            >
                {unreadCount > 0 ? (
                    <BellRing size={20} className="animate-pulse" />
                ) : (
                    <Bell size={20} />
                )}
                {unreadCount > 0 && (
                    <span className="absolute -top-1 -right-1 flex h-5 w-5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-5 w-5 bg-red-500 text-white text-[10px] font-bold items-center justify-center">
                            {unreadCount}
                        </span>
                    </span>
                )}
            </button>

            {/* Notification Panel */}
            {isOpen && (
                <>
                    {/* Backdrop */}
                    <div
                        className="fixed inset-0 z-40"
                        onClick={() => setIsOpen(false)}
                    />

                    {/* Panel */}
                    <div className="absolute right-0 top-12 w-96 bg-white rounded-2xl shadow-2xl border border-slate-200 z-50 max-h-[600px] flex flex-col">
                        {/* Header */}
                        <div className="flex items-center justify-between p-4 border-b border-slate-200">
                            <h3 className="font-bold text-slate-800">通知中心</h3>
                            {unreadCount > 0 && (
                                <button
                                    onClick={markAllAsRead}
                                    className="text-xs text-indigo-600 hover:text-indigo-700 font-medium"
                                >
                                    全部已读
                                </button>
                            )}
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto">
                            {notifications.length === 0 ? (
                                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                                    <Bell size={48} className="mb-3 opacity-50" />
                                    <p className="text-sm font-medium">暂无通知</p>
                                </div>
                            ) : (
                                <div className="divide-y divide-slate-100">
                                    {notifications.map((notification) => (
                                        <div
                                            key={notification.id}
                                            className={cn(
                                                "p-4 cursor-pointer transition-all",
                                                getNotificationColor(notification.type),
                                                !notification.read && "border-l-4 border-l-indigo-500"
                                            )}
                                            onClick={() => {
                                                markAsRead(notification.id);
                                                if (notification.url) {
                                                    window.open(notification.url, '_blank');
                                                }
                                            }}
                                        >
                                            <div className="flex items-start gap-3">
                                                <span className="text-xl">{getNotificationIcon(notification.type)}</span>
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center justify-between gap-2 mb-1">
                                                        <h4 className="font-bold text-slate-800 text-sm">
                                                            {notification.title}
                                                        </h4>
                                                        {!notification.read && (
                                                            <span className="flex-shrink-0 w-2 h-2 bg-indigo-500 rounded-full"></span>
                                                        )}
                                                    </div>
                                                    <p className="text-sm text-slate-600 mb-2">
                                                        {notification.message}
                                                    </p>
                                                    <div className="flex items-center gap-2 text-xs text-slate-400">
                                                        <Clock size={12} />
                                                        <span>
                                                            {formatDistanceToNow(notification.timestamp)}
                                                        </span>
                                                        {notification.url && (
                                                            <ExternalLink size={12} className="ml-auto" />
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Footer */}
                        <div className="p-3 border-t border-slate-200 bg-slate-50">
                            <p className="text-xs text-slate-400 text-center">
                                {permission === 'granted' ? '✅ 已启用桌面通知' : '⚠️ 未启用桌面通知'}
                            </p>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}

function formatDistanceToNow(date: Date): string {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);

    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return `${Math.floor(seconds / 86400)} 天前`;
}
