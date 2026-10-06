"use client";

import React, { useEffect, useRef } from 'react';

interface ModalOverlayProps {
    open: boolean;
    onClose: () => void;
    labelledBy?: string;
    children: React.ReactNode;
    /**
     * 遮罩层整组类名（z-index、背景、内边距、fixed/absolute 等）。
     * 不传时使用与 FilterModal 一致的默认遮罩；传入时整体替换，
     * 避免同一 CSS 属性的两个工具类竞争生效顺序。
     */
    zIndexClass?: string;
}

const DEFAULT_OVERLAY_CLASS = 'fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-3 backdrop-blur-sm sm:p-4';

export default function ModalOverlay({ open, onClose, labelledBy, children, zIndexClass }: ModalOverlayProps) {
    const dialogRef = useRef<HTMLDivElement>(null);
    const onCloseRef = useRef(onClose);

    useEffect(() => {
        onCloseRef.current = onClose;
    }, [onClose]);

    useEffect(() => {
        if (!open) return;

        const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        // 若子元素（如 autoFocus 输入框）已接管焦点，则不抢占
        const dialog = dialogRef.current;
        if (dialog && !dialog.contains(document.activeElement)) dialog.focus();

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onCloseRef.current();
        };
        document.addEventListener('keydown', handleKeyDown);
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            previousFocus?.focus();
        };
    }, [open]);

    if (!open) return null;

    return (
        <div className={zIndexClass ?? DEFAULT_OVERLAY_CLASS} onClick={onClose}>
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={labelledBy}
                tabIndex={-1}
                className="flex w-full flex-col items-center"
                onClick={(event) => event.stopPropagation()}
            >
                {children}
            </div>
        </div>
    );
}
