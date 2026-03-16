"""
多数据源同步模块
支持多个数据源自动切换，提高数据同步可靠性
"""
import time
import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum

import pandas as pd
import akshare as ak
from sqlalchemy import text

from core.db import get_db_engine, save_to_db, validate_stock_code
from core.logging_config import logger
from core.config import config


class DataSourceStatus(Enum):
    """数据源状态"""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class DataSource(ABC):
    """
    数据源抽象基类
    所有数据源都需要实现这个接口
    """

    def __init__(self, name: str):
        self.name = name
        self.status = DataSourceStatus.UNKNOWN
        self.fail_count = 0
        self.success_count = 0
        self.last_check_time: Optional[datetime] = None
        self.last_error: Optional[str] = None

    @abstractmethod
    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """
        获取股票列表

        Returns:
            DataFrame with columns: [code, name, industry, ...]
        """
        pass

    @abstractmethod
    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        """
        获取历史数据

        Args:
            code: 股票代码（6位数字）
            start_date: 开始日期 (YYYYMMDD)

        Returns:
            DataFrame with columns: [日期, 开盘, 最高, 最低, 收盘, 成交量]
        """
        pass

    def format_code(self, code: str) -> str:
        """
        格式化股票代码为该数据源所需的格式

        Args:
            code: 6位股票代码

        Returns:
            格式化后的代码
        """
        return code

    def check_status(self) -> DataSourceStatus:
        """
        检查数据源状态

        Returns:
            数据源状态
        """
        # 简单的健康检查 - 尝试获取一只股票的数据
        try:
            test_code = "000001"
            df = self.get_hist_data(test_code, "20240101")
            if df is not None and not df.empty:
                self.status = DataSourceStatus.AVAILABLE
                self.success_count += 1
                self.fail_count = 0
            else:
                self.status = DataSourceStatus.UNKNOWN
        except Exception as e:
            self.fail_count += 1
            self.last_error = str(e)[:100]

            # 根据错误类型判断状态
            if "timeout" in str(e).lower() or "connection" in str(e).lower():
                self.status = DataSourceStatus.UNAVAILABLE
            elif "rate" in str(e).lower() or "429" in str(e):
                self.status = DataSourceStatus.RATE_LIMITED
            else:
                self.status = DataSourceStatus.UNKNOWN

        self.last_check_time = datetime.now()
        return self.status

    def is_available(self) -> bool:
        """检查数据源是否可用"""
        if self.status == DataSourceStatus.RATE_LIMITED:
            # 限流状态，等待一段时间后重试
            if self.last_check_time:
                elapsed = (datetime.now() - self.last_check_time).total_seconds()
                if elapsed > 300:  # 5分钟后重试
                    return self.check_status() == DataSourceStatus.AVAILABLE
            return False

        if self.fail_count >= 3:
            # 连续失败3次，标记为不可用
            self.status = DataSourceStatus.UNAVAILABLE
            return False

        return self.status == DataSourceStatus.AVAILABLE

    def record_success(self):
        """记录成功调用"""
        self.success_count += 1
        self.fail_count = 0
        self.status = DataSourceStatus.AVAILABLE

    def record_failure(self, error: str):
        """记录失败调用"""
        self.fail_count += 1
        self.last_error = error[:100]
        if self.fail_count >= 3:
            self.status = DataSourceStatus.UNAVAILABLE


class EastMoneyDataSource(DataSource):
    """
    东方财富数据源
    接口：stock_zh_a_hist_em(), stock_zh_a_spot_em()
    """

    def __init__(self):
        super().__init__("东方财富")
        self.priority = 1  # 优先级（1最高）

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        try:
            df = ak.stock_zh_a_spot_em()
            return df[['代码', '名称', '所属行业']].copy()
        except Exception as e:
            logger.warning(f"东方财富获取股票列表失败: {e}")
            return None

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                adjust="qfq"
            )
            return df
        except Exception as e:
            raise

    @staticmethod
    def format_code(code: str) -> str:
        return code


class SinaDataSource(DataSource):
    """
    新浪财经数据源
    接口：stock_zh_a_daily(), stock_zh_a_spot()
    """

    def __init__(self):
        super().__init__("新浪财经")
        self.priority = 2

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        try:
            df = ak.stock_zh_a_spot()
            return df[['代码', '名称', '所属行业']].copy()
        except Exception as e:
            logger.warning(f"新浪财经获取股票列表失败: {e}")
            return None

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        try:
            df = ak.stock_zh_a_daily(
                symbol=code,
                adjust="qfq"
            )
            if df.empty:
                return None

            # 新浪返回的列名是英文，需要转换
            df = df.rename(columns={
                'date': '日期',
                'open': '开盘',
                'high': '最高',
                'low': '最低',
                'close': '收盘',
                'volume': '成交量'
            })

            # 筛选日期
            start_dt = datetime.strptime(start_date, "%Y%m%d")
            df['日期'] = pd.to_datetime(df['日期']).dt.date
            df = df[df['日期'] >= start_dt.date()]

            return df
        except Exception as e:
            raise


class TencentDataSource(DataSource):
    """
    腾讯财经数据源
    接口：stock_zh_a_hist_tx()
    稳定性最好，但返回成交额而非成交量
    """

    def __init__(self):
        super().__init__("腾讯财经")
        self.priority = 3

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        # 腾讯没有直接获取所有股票的接口，使用新浪的
        try:
            df = ak.stock_zh_a_spot()
            return df[['代码', '名称', '所属行业']].copy()
        except Exception as e:
            logger.warning(f"腾讯财经获取股票列表失败: {e}")
            return None

    def format_code(self, code: str) -> str:
        """腾讯需要带交易所前缀"""
        if code.startswith('6'):
            return f'sh{code}'
        elif code.startswith('0') or code.startswith('3'):
            return f'sz{code}'
        elif code.startswith('8') or code.startswith('4'):
            return f'bj{code}'
        return code

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        try:
            symbol = self.format_code(code)
            df = ak.stock_zh_a_hist_tx(symbol=symbol)

            if df.empty:
                return None

            # 腾讯返回的列名：date, open, close, high, low, amount
            df = df.rename(columns={
                'date': '日期',
                'open': '开盘',
                'high': '最高',
                'low': '最低',
                'close': '收盘',
                'amount': '成交额'
            })

            # 腾讯没有成交量，用成交额填充
            df['成交量'] = df['成交额']

            # 筛选日期
            start_dt = datetime.strptime(start_date, "%Y%m%d")
            df['日期'] = pd.to_datetime(df['日期']).dt.date
            df = df[df['日期'] >= start_dt.date()]

            return df
        except Exception as e:
            raise


class TushareDataSource(DataSource):
    """
    Tushare Pro 数据源
    需要提供 TUSHARE_TOKEN
    """

    def __init__(self, token: Optional[str] = None):
        super().__init__("Tushare")
        self.token = token or config.TUSHARE_TOKEN
        self.priority = 0  # 最高优先级
        self.pro = None
        
        if self.token:
            try:
                import tushare as ts
                self.pro = ts.pro_api(self.token)
                self.status = DataSourceStatus.AVAILABLE
            except Exception as e:
                logger.error(f"Tushare 初始化失败: {e}")
                self.status = DataSourceStatus.UNAVAILABLE
        else:
            self.status = DataSourceStatus.UNAVAILABLE
            self.last_error = "未配置 TUSHARE_TOKEN"

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        if not self.pro:
            return None
        try:
            # 获取上市股票列表
            df = self.pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,industry')
            if df.empty:
                return None
            
            # 格式化列名
            df = df.rename(columns={
                'symbol': '代码',
                'name': '名称',
                'industry': '所属行业'
            })
            return df[['代码', '名称', '所属行业']]
        except Exception as e:
            logger.warning(f"Tushare 获取股票列表失败: {e}")
            return None

    def format_code(self, code: str) -> str:
        """Tushare 需要 ts_code (例如 000001.SZ)"""
        if code.startswith('6'):
            return f'{code}.SH'
        elif code.startswith('0') or code.startswith('3'):
            return f'{code}.SZ'
        elif code.startswith('8') or code.startswith('4'):
            return f'{code}.BJ'
        return code

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        if not self.pro:
            return None
        try:
            ts_code = self.format_code(code)
            # Tushare pro daily 接口
            df = self.pro.daily(ts_code=ts_code, start_date=start_date)
            
            if df.empty:
                return None

            # Tushare 返回列: ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount
            # 需要转换为: 日期, 开盘, 最高, 最低, 收盘, 成交量
            df = df.rename(columns={
                'trade_date': '日期',
                'open': '开盘',
                'high': '最高',
                'low': '最低',
                'close': '收盘',
                'vol': '成交量'
            })
            
            # Tushare 的日期格式是 YYYYMMDD，转换为 YYYY-MM-DD
            df['日期'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-%d')
            
            # 按日期升序排列
            df = df.sort_values('日期')
            
            return df[['日期', '开盘', '最高', '最低', '收盘', '成交量']]
        except Exception as e:
            raise


class DataSourceManager:
    """
    数据源管理器
    负责管理多个数据源，自动切换和负载均衡
    """

    def __init__(self):
        self.sources: List[DataSource] = []
        self.current_index = 0

        # 初始化所有数据源
        self._init_sources()

        # 检查数据源状态
        self._check_all_sources()

    def _init_sources(self):
        """初始化数据源列表"""
        self.sources = [
            TushareDataSource(),
            EastMoneyDataSource(),
            SinaDataSource(),
            TencentDataSource(),
        ]

        # 按优先级排序
        self.sources.sort(key=lambda x: x.priority)

    def _check_all_sources(self):
        """检查所有数据源状态"""
        logger.info("检查数据源状态...")
        for source in self.sources:
            status = source.check_status()
            logger.info(f"  {source.name}: {status.value}")

    def get_available_source(self) -> Optional[DataSource]:
        """
        获取当前可用的数据源

        Returns:
            可用的数据源，如果没有可用的则返回 None
        """
        # 先按优先级尝试
        for source in self.sources:
            if source.is_available():
                return source

        # 都不可用，重新检查一次
        logger.warning("所有数据源不可用，重新检查...")
        self._check_all_sources()

        for source in self.sources:
            if source.is_available():
                return source

        return None

    def get_source(self, source_name: str) -> Optional[DataSource]:
        """
        根据名称获取数据源

        Args:
            source_name: 数据源名称

        Returns:
            数据源对象
        """
        for source in self.sources:
            if source.name == source_name:
                return source
        return None

    def rotate_source(self) -> Optional[DataSource]:
        """
        轮换到下一个数据源（负载均衡）

        Returns:
            下一个可用的数据源
        """
        for _ in range(len(self.sources)):
            self.current_index = (self.current_index + 1) % len(self.sources)
            source = self.sources[self.current_index]
            if source.is_available():
                return source
        return None

    def get_status_report(self) -> Dict[str, Any]:
        """
        获取所有数据源的状态报告

        Returns:
            状态报告字典
        """
        report = {}
        for source in self.sources:
            report[source.name] = {
                "status": source.status.value,
                "priority": source.priority,
                "success_count": source.success_count,
                "fail_count": source.fail_count,
                "last_error": source.last_error,
                "last_check": source.last_check_time.isoformat() if source.last_check_time else None
            }
        return report


class MultiSourceSync:
    """
    多数据源同步器
    支持自动切换数据源
    """

    def __init__(self, manager: Optional[DataSourceManager] = None):
        self.manager = manager or DataSourceManager()
        self.engine = get_db_engine()

    def sync_single_stock(self, code: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        同步单只股票，自动切换数据源

        Args:
            code: 股票代码
            max_retries: 最大重试次数

        Returns:
            同步结果字典
        """
        result = {
            "code": code,
            "success": False,
            "source": None,
            "records": 0,
            "message": ""
        }

        # 获取最后日期
        try:
            with self.engine.connect() as conn:
                res = conn.execute(
                    text("SELECT MAX(date) FROM daily_k WHERE code = :code"),
                    {"code": code}
                )
                last_date = res.fetchone()[0]

            # 检查是否需要同步
            today = datetime.now().date()
            if last_date:
                if (today - last_date).days <= 1:
                    result["success"] = True
                    result["message"] = "已是最新"
                    return result
                start_date = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            else:
                # 无历史数据，获取最近一年的数据
                start_date = (today - timedelta(days=365)).strftime("%Y%m%d")

        except Exception as e:
            result["message"] = f"查询失败: {e}"
            return result

        # 尝试使用不同数据源
        for attempt in range(max_retries):
            source = self.manager.get_available_source()

            if not source:
                result["message"] = "无可用数据源"
                return result

            try:
                df = source.get_hist_data(code, start_date)

                if df is not None and not df.empty:
                    save_to_db(df, code, self.engine)
                    source.record_success()

                    result["success"] = True
                    result["source"] = source.name
                    result["records"] = len(df)
                    result["message"] = f"成功同步 {len(df)} 条"
                    return result
                else:
                    # 无新数据也算成功
                    source.record_success()
                    result["success"] = True
                    result["source"] = source.name
                    result["message"] = "无新数据"
                    return result

            except Exception as e:
                logger.warning(f"{source.name} 同步 {code} 失败: {str(e)}")
                source.record_failure(str(e))

                # 切换到下一个数据源
                self.manager.rotate_source()

                # 短暂延迟
                time.sleep(1)

        result["message"] = "所有数据源均失败"
        return result

    def sync_batch(self, codes: List[str], delay_range=(1.2, 2.5), progress_callback=None) -> Dict[str, Any]:
        """
        批量同步股票

        Args:
            codes: 股票代码列表
            delay_range: 请求间隔范围
            progress_callback: 进度回调函数 callback(current, total, success, failed)

        Returns:
            同步结果汇总
        """
        results = {
            "total": len(codes),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "details": []
        }

        for i, code in enumerate(codes, 1):
            result = self.sync_single_stock(code)
            results["details"].append(result)

            if result["success"]:
                if "已是最新" in result["message"] or "无新数据" in result["message"]:
                    results["skipped"] += 1
                else:
                    results["success"] += 1
                    logger.debug(f"[{i}/{len(codes)}] {code}: {result['message']} (来源: {result['source']})")
            else:
                results["failed"] += 1
                logger.warning(f"[{i}/{len(codes)}] {code}: {result['message']}")

            # 调用进度回调
            if progress_callback:
                progress_callback(i, len(codes), results["success"], results["failed"])

            # 随机延迟
            time.sleep(random.uniform(*delay_range))

            # 每50只报告进度
            if i % 50 == 0:
                logger.info(f"进度: {i}/{len(codes)}, 成功: {results['success']}, "
                          f"失败: {results['failed']}, 跳过: {results['skipped']}")

        return results

    def print_status(self):
        """打印数据源状态"""
        report = self.manager.get_status_report()
        print("\n" + "="*60)
        print("数据源状态")
        print("="*60)

        for name, info in report.items():
            status_icon = "✅" if info["status"] == "available" else "❌"
            print(f"{status_icon} {name} (优先级: {info['priority']})")
            print(f"   状态: {info['status']}")
            print(f"   成功: {info['success_count']}, 失败: {info['fail_count']}")
            if info['last_error']:
                print(f"   最后错误: {info['last_error']}")

        print("="*60)


def main():
    """主函数 - 演示多数据源同步"""
    from sqlalchemy import text

    print("="*60)
    print("多数据源同步")
    print("="*60)

    # 初始化同步器
    syncer = MultiSourceSync()

    # 打印数据源状态
    syncer.print_status()

    # 获取需要同步的股票
    engine = get_db_engine()
    with engine.connect() as conn:
        res = conn.execute(text("""
            SELECT DISTINCT code FROM daily_k
            ORDER BY code
            LIMIT 100
        """)).fetchall()

    codes = [row[0] for row in res]
    print(f"\n需要同步 {len(codes)} 只股票")

    # 执行同步
    results = syncer.sync_batch(codes, delay_range=(0.5, 1.0))

    # 打印结果
    print("\n" + "="*60)
    print("同步完成")
    print(f"总计: {results['total']}")
    print(f"成功: {results['success']}")
    print(f"跳过: {results['skipped']}")
    print(f"失败: {results['failed']}")
    print("="*60)


if __name__ == "__main__":
    main()
