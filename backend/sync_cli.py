#!/usr/bin/env python3
"""
多数据源同步命令行工具
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.multi_source_sync import DataSourceManager, MultiSourceSync
from core.logging_config import logger
from sqlalchemy import text


def cmd_status(args):
    """查看数据源状态"""
    manager = DataSourceManager()
    report = manager.get_status_report()

    print("\n" + "="*60)
    print("数据源状态")
    print("="*60)

    for name, info in report.items():
        status_icons = {
            "available": "✅",
            "unavailable": "❌",
            "rate_limited": "⏳",
            "unknown": "❓"
        }
        icon = status_icons.get(info["status"], "❓")
        print(f"\n{icon} {name} (优先级: {info['priority']})")
        print(f"   状态: {info['status']}")
        print(f"   成功: {info['success_count']}, 失败: {info['fail_count']}")

        if info.get("last_error"):
            print(f"   最后错误: {info['last_error']}")

    print("\n" + "="*60)


def cmd_sync(args):
    """执行同步"""
    manager = DataSourceManager()

    # 打印状态
    print("\n" + "="*60)
    print("数据源状态")
    print("="*60)

    for source in manager.sources:
        if source.is_available():
            print(f"  ✅ {source.name} (可用)")
        else:
            print(f"  ❌ {source.name} (不可用: {source.status.value})")

    # 初始化同步器
    syncer = MultiSourceSync(manager)

    # 获取同步股票
    engine = syncer.engine

    with engine.connect() as conn:
        if args.code:
            # 同步指定股票
            codes = [args.code]
        elif args.limit:
            # 同步前N只股票
            res = conn.execute(text("""
                SELECT DISTINCT code FROM daily_k
                ORDER BY code
                LIMIT :limit
            """), {"limit": args.limit}).fetchall()
            codes = [row[0] for row in res]
        else:
            # 同步所有股票
            res = conn.execute(text("""
                SELECT DISTINCT code FROM daily_k
                ORDER BY code
            """)).fetchall()
            codes = [row[0] for row in res]

    print(f"\n需要同步 {len(codes)} 只股票...")

    # 执行同步
    results = syncer.sync_batch(
        codes,
        delay_range=(args.delay_min, args.delay_max)
    )

    # 打印结果
    print("\n" + "="*60)
    print("同步完成")
    print("="*60)
    print(f"总计: {results['total']}")
    print(f"成功: {results['success']}")
    print(f"跳过: {results['skipped']}")
    print(f"失败: {results['failed']}")

    if args.verbose and results['failed'] > 0:
        print("\n失败的股票:")
        for detail in results['details']:
            if not detail['success']:
                print(f"  {detail['code']}: {detail['message']}")


def cmd_check(args):
    """检查单个股票"""
    manager = DataSourceManager()

    print(f"\n检查股票: {args.code}")

    for source in manager.sources:
        if source.is_available():
            try:
                print(f"\n  尝试 {source.name}...")
                df = source.get_hist_data(args.code, "20240101")

                if df is not None and not df.empty:
                    print(f"    ✅ 成功! 获取 {len(df)} 条记录")
                    print(f"    日期范围: {df['日期'].min()} 至 {df['日期'].max()}")
                else:
                    print(f"    ⚠️  无数据")
            except Exception as e:
                print(f"    ❌ 失败: {str(e)[:50]}...")


def main():
    parser = argparse.ArgumentParser(
        description="多数据源股票数据同步工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s status          # 查看数据源状态
  %(prog)s sync            # 同步所有股票
  %(prog)s sync --limit 100  # 同步前100只股票
  %(prog)s sync --code 000001  # 同步指定股票
  %(prog)s check --code 000001  # 检查指定股票
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # status 命令
    status_parser = subparsers.add_parser('status', help='查看数据源状态')
    status_parser.set_defaults(func=cmd_status)

    # sync 命令
    sync_parser = subparsers.add_parser('sync', help='执行数据同步')
    sync_parser.add_argument('--limit', type=int, help='限制同步数量')
    sync_parser.add_argument('--code', type=str, help='同步指定股票')
    sync_parser.add_argument('--delay-min', type=float, default=0.3, help='最小延迟')
    sync_parser.add_argument('--delay-max', type=float, default=0.8, help='最大延迟')
    sync_parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    sync_parser.set_defaults(func=cmd_sync)

    # check 命令
    check_parser = subparsers.add_parser('check', help='检查单个股票')
    check_parser.add_argument('--code', type=str, required=True, help='股票代码')
    check_parser.set_defaults(func=cmd_check)

    args = parser.parse_args()

    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
