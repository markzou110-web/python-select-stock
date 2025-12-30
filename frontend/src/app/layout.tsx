import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Alpha Vision | 极光量化终端",
  description: "专业均线粘合与强共振突破监控系统 (v5.0 Pro)",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${inter.className} antialiased text-slate-900 bg-slate-50`}>
        {children}
      </body>
    </html>
  );
}
