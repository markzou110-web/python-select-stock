// CSV 导出统一工具。
// toCsvString 的转义/BOM/换行逻辑从 ResultsTable 原内联实现原样抽取，保证输出逐字节一致。

export function toCsvString(headers: string[], rows: (string | number | null | undefined)[][]): string {
    const escapeCell = (cell: string | number | null | undefined) => {
        const str = String(cell ?? '');
        return str.includes(',') || str.includes('"') || str.includes('\n')
            ? `"${str.replace(/"/g, '""')}"`
            : str;
    };
    // BOM for Excel UTF-8 compatibility
    return '\uFEFF' + [headers, ...rows].map(row => row.map(escapeCell).join(',')).join('\n');
}

export function downloadCsv(filename: string, csv: string): void {
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    // 等点击触发的下载启动后再回收，避免个别浏览器因 URL 提前失效导致下载失败
    setTimeout(() => URL.revokeObjectURL(url), 0);
}
