import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs))
}

export function clampScore(value: number | null | undefined): number {
    const score = Number(value)
    return Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : 0
}
