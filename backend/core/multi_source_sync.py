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
import threading
from sqlalchemy import text

from core.db import get_db_engine, save_to_db, validate_stock_code
from core.logging_config import logger
from core.config import config


MIN_REQUIRED_TRADING_DAYS = 650
HISTORY_LOOKBACK_CALENDAR_DAYS = 1010

# 批量同步并发度。DB 连接池上限为 30（pool_size=10 + max_overflow=20），
# 24 个 worker 既能充分并行又留有余量；akshare 调用为 I/O bound，GIL 不阻塞。
# 如遇数据源限流，可下调此值。
SYNC_MAX_WORKERS = 24

# 单只股票同步的总超时（秒），防止个别源卡死拖垮整批同步。
SYNC_SINGLE_STOCK_TIMEOUT = 30

# 数据源切换时的短延迟范围（秒），仅在源失败后用于轻微错峰，
# 避免对同一源瞬时重试。原值为固定 1s，对批量场景过重。
SOURCE_FAILOVER_DELAY_RANGE = (0.1, 0.3)

# 腾讯源单股重试退避起始秒数与倍率（指数退避，缩短原 0.5~1.5s 的固定退避）。
TENCENT_RETRY_BASE_DELAY = 0.2
TENCENT_RETRY_BACKOFF_FACTOR = 1.5


class StockNotSupportedError(Exception):
    """某数据源确定不支持该股票（如已退市/B股/解析失败）。

    抛出此异常意味着无需重试也不必切换到下一个数据源——
    对该股票而言所有源大概率都会失败。sync_single_stock 捕获后直接短路返回。
    """


def needs_history_backfill(data_count: int) -> bool:
    return data_count < MIN_REQUIRED_TRADING_DAYS


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
        self.last_error = error[:100]
        # 忽略部分不支持的股票解析异常，不统计为数据源崩溃
        if any(err in error for err in ["list index", "invalid literal", "KeyError", "not found"]):
            return
            
        self.fail_count += 1
        
        # 补充：检测是否因为限流导致的失败
        if any(keyword in error.lower() for keyword in ["rate", "429", "每分钟", "接口访问", "访问该接口"]):
            self.status = DataSourceStatus.RATE_LIMITED
            self.last_check_time = datetime.now()
            logger.info(f"数据源 {self.name} 已触发限流，进入等待状态...")
            
        elif self.fail_count >= 3:
            self.status = DataSourceStatus.UNAVAILABLE


class EastMoneyDataSource(DataSource):
    """
    东方财富数据源
    接口：stock_zh_a_hist_em(), stock_zh_a_spot_em()
    """

    def __init__(self):
        super().__init__("东方财富")
        self.priority = 3  # 被代理/网络封锁，降级

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        try:
            df = ak.stock_zh_a_spot_em()
            # 检查可用列，akshare 可能会更改列名
            available_cols = df.columns.tolist()
            required_cols = []
            if '代码' in available_cols:
                required_cols.append('代码')
            if '名称' in available_cols:
                required_cols.append('名称')
            if '所属行业' in available_cols:
                required_cols.append('所属行业')

            if not required_cols or '代码' not in available_cols:
                logger.warning(f"东方财富获取股票列表失败: 缺少必要列，可用列: {available_cols[:5]}")
                return None

            result = df[required_cols].copy()
            # 如果没有行业列，添加默认值
            if '所属行业' not in result.columns:
                result['所属行业'] = '未知'
            return result
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
            # 检查可用列，akshare 可能会更改列名
            available_cols = df.columns.tolist()
            required_cols = []
            if '代码' in available_cols:
                required_cols.append('代码')
            if '名称' in available_cols:
                required_cols.append('名称')
            if '所属行业' in available_cols:
                required_cols.append('所属行业')

            if not required_cols or '代码' not in available_cols:
                logger.warning(f"新浪财经获取股票列表失败: 缺少必要列，可用列: {available_cols[:5]}")
                return None

            result = df[required_cols].copy()
            # 如果没有行业列，添加默认值
            if '所属行业' not in result.columns:
                result['所属行业'] = '未知'
            return result
        except Exception as e:
            logger.warning(f"新浪财经获取股票列表失败: {e}")
            return None

    def format_code(self, code: str) -> str:
        """新浪需要带交易所前缀"""
        if code.startswith('6') or code.startswith('900'):
            return f'sh{code}'
        elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
            return f'sz{code}'
        elif code.startswith('8') or code.startswith('4') or code.startswith('920'):
            return f'bj{code}'
        return code

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        try:
            symbol = self.format_code(code)
            df = ak.stock_zh_a_daily(
                symbol=symbol,
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
            
            # 新浪的成交量单位是股，我们需要统一转换为手 (100股=1手)
            df['成交量'] = df['成交量'] / 100.0

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
        self.priority = 0  # 优先级最高，当前网络最稳定

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        # 腾讯没有直接获取所有股票的接口，使用新浪的
        try:
            df = ak.stock_zh_a_spot()
            # 检查可用列，akshare 可能会更改列名
            available_cols = df.columns.tolist()
            required_cols = []
            if '代码' in available_cols:
                required_cols.append('代码')
            if '名称' in available_cols:
                required_cols.append('名称')
            if '所属行业' in available_cols:
                required_cols.append('所属行业')

            if not required_cols or '代码' not in available_cols:
                logger.warning(f"腾讯财经获取股票列表失败: 缺少必要列，可用列: {available_cols[:5]}")
                return None

            result = df[required_cols].copy()
            # 如果没有行业列，添加默认值
            if '所属行业' not in result.columns:
                result['所属行业'] = '未知'
            return result
        except Exception as e:
            logger.warning(f"腾讯财经获取股票列表失败: {e}")
            return None

    def format_code(self, code: str) -> str:
        """腾讯需要带交易所前缀"""
        # 60xxxx/68xxxx 为沪市主板/科创板，900xxx 为沪市B股
        if code.startswith('6') or code.startswith('900'):
            return f'sh{code}'
        # 00xxxx/30xxxx 为深市主板/创业板，20xxxx 为深市B股
        elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
            return f'sz{code}'
        # 8xxxxx/4xxxxx/920xxx 为北交所/新三板
        elif code.startswith('8') or code.startswith('4') or code.startswith('920'):
            return f'bj{code}'
        return f'sz{code}'

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        symbol = self.format_code(code)
        df = None
        delay = TENCENT_RETRY_BASE_DELAY
        for attempt in range(5):
            try:
                # Add dates to make it specific and avoid pulling 20 years of history if not needed.
                start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" # Convert YYYYMMDD to YYYY-MM-DD for Tencent
                end = datetime.now().strftime("%Y-%m-%d")
                df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end)
                break
            except Exception as e:
                err_msg = str(e)
                # 确定性的"不支持该股票"错误：立即短路，不重试也不切换数据源。
                # 这些错误表示股票代码无法被解析/已退市/不存在，重试毫无意义。
                if any(tag in err_msg for tag in ["list index", "invalid literal", "KeyError", "not found"]):
                    raise StockNotSupportedError(f"腾讯源不支持 {code}: {err_msg}") from e
                logger.debug(f"腾讯数据源尝试 {attempt+1}/5 失败 ({code}): {e}")
                if attempt == 4:
                    raise
                # 指数退避：0.2 → 0.3 → 0.45 → 0.68（总等待 ~1.6s，原固定 0.5~1.5s 共 ~6s）
                time.sleep(delay + random.uniform(0, 0.1))
                delay *= TENCENT_RETRY_BACKOFF_FACTOR

        if df is None or df.empty:
            return None

        # 腾讯返回的列名中的 amount 实际上是成交量（手）
        df = df.rename(columns={
            'date': '日期',
            'open': '开盘',
            'high': '最高',
            'low': '最低',
            'close': '收盘',
            'amount': '成交量'
        })

        # 筛选日期
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        df['日期'] = pd.to_datetime(df['日期']).dt.date
        df = df[df['日期'] >= start_dt.date()]

        return df


class TushareDataSource(DataSource):
    """
    Tushare Pro 数据源
    需要提供 TUSHARE_TOKEN
    """

    # 类级别锁和频率限制器 (全实例共享，确保多线程同步时不超频)
    _lock = threading.Lock()
    _last_call_time = 0.0
    _min_interval = 1.3  # 60秒/50次 = 1.2s，取1.3s更安全

    def __init__(self, token: Optional[str] = None):
        super().__init__("Tushare")
        self.token = token or config.TUSHARE_TOKEN
        self.priority = 1  # 提高优先级，因为 Tushare 提供完整的行业信息
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

    def _reserve_slot(self) -> float:
        """令牌桶式预约下一个 Tushare 调用时间片。

        在锁临界区内只做时间戳的计算与预约（更新 _last_call_time 为"下一个可用时刻"），
        返回调用方需要等待的秒数。真正的 time.sleep 在锁外执行——这样多个线程可以
        并发地预约各自的时间片而不会在 sleep 期间互相阻塞，在严格遵守 Tushare
        频率限制（≥ _min_interval 秒/次）的前提下大幅提升并行度。
        """
        with TushareDataSource._lock:
            now = time.time()
            earliest = max(now, TushareDataSource._last_call_time + TushareDataSource._min_interval)
            wait = earliest - now
            TushareDataSource._last_call_time = earliest
        return wait

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        if not self.pro:
            return None
        try:
            # 频率控制：令牌桶式预约，sleep 在锁外执行以允许跨线程交错。
            wait = self._reserve_slot()
            if wait > 0:
                time.sleep(wait)

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
            # 填充缺失的行业信息
            df['所属行业'] = df['所属行业'].fillna('未知')
            return df[['代码', '名称', '所属行业']]
        except Exception as e:
            logger.warning(f"Tushare 获取股票列表失败: {e}")
            return None

    def format_code(self, code: str) -> str:
        """Tushare 需要 ts_code (例如 000001.SZ)"""
        if code.startswith('6') or code.startswith('900'):
            return f'{code}.SH'
        elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
            return f'{code}.SZ'
        elif code.startswith('8') or code.startswith('4') or code.startswith('920'):
            return f'{code}.BJ'
        return code

    def get_hist_data(self, code: str, start_date: str) -> Optional[pd.DataFrame]:
        if not self.pro:
            return None
        try:
            ts_code = self.format_code(code)

            # 频率控制：令牌桶式预约，sleep 在锁外执行以允许跨线程交错。
            wait = self._reserve_slot()
            if wait > 0:
                time.sleep(wait)

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

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """
        从所有可用数据源尝试获取股票列表

        Returns:
            DataFrame with columns: [code, name, industry]
        """
        for source in self.manager.sources:
            if not source.is_available():
                continue
            try:
                df = source.get_stock_list()
                if df is not None and not df.empty:
                    # 统一格式为英文列名：code, name, industry
                    df = df.rename(columns={'代码': 'code', '名称': 'name', '所属行业': 'industry'})
                    
                    # 核心改动：清理代码格式，确保统一为6位纯数字字符串
                    def clean_code(c):
                        try:
                            c_str = str(c).strip().lower()
                            # 剔除 sh/sz/bj 前缀
                            if c_str.startswith(('sz', 'sh', 'bj')):
                                c_str = c_str[2:]
                            # 剔除 .0 (来自之前的 float 转换错误)
                            if c_str.endswith('.0'):
                                c_str = c_str[:-2]
                            # 补齐6位 (例如 '1' -> '000001')
                            if c_str.isdigit() and len(c_str) < 6:
                                c_str = c_str.zfill(6)
                            return c_str
                        except:
                            return str(c)
                            
                    df['code'] = df['code'].apply(clean_code)
                    # 剔除无意义的占位名（深市、沪市等）
                    df = df[~df['name'].str.contains('深市|沪市', na=False)]
                    # 剔除北交所股票 (43, 83, 87, 88, 92 等开头)
                    df = df[~df['code'].str.startswith(('4', '8', '92'))]
                    # 去重
                    df = df.drop_duplicates(subset=['code'])
                    
                    source.record_success()
                    return df
            except Exception as e:
                source.record_failure(str(e))
                logger.warning(f"数据源 {source.name} 获取股票列表失败: {e}")
        return None

    def sync_single_stock(self, code: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        同步单只股票，自动切换数据源。

        对实际同步逻辑套一层总超时保护（SYNC_SINGLE_STOCK_TIMEOUT 秒），防止
        个别股票因某个数据源卡死而拖垮整批同步。超时则记为失败并返回，不影响其它股票。

        Args:
            code: 股票代码
            max_retries: 最大重试次数

        Returns:
            同步结果字典
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

        # 复用一个单线程池执行，避免为每只股票新建线程的开销；超时后线程仍会
        # 在后台跑完（无法强制中断 Python 线程），但结果被丢弃，不阻塞批次。
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._sync_single_stock_impl, code, max_retries)
            try:
                return future.result(timeout=SYNC_SINGLE_STOCK_TIMEOUT)
            except FuturesTimeoutError:
                logger.warning(f"同步 {code} 超时（>{SYNC_SINGLE_STOCK_TIMEOUT}s），跳过")
                return {
                    "code": code,
                    "success": False,
                    "source": None,
                    "records": 0,
                    "message": f"同步超时（>{SYNC_SINGLE_STOCK_TIMEOUT}s）",
                }

    def _sync_single_stock_impl(self, code: str, max_retries: int = 3) -> Dict[str, Any]:
        """sync_single_stock 的实际实现（无超时保护）。"""
        result = {
            "code": code,
            "success": False,
            "source": None,
            "records": 0,
            "message": ""
        }

        # 获取最后日期和数据天数
        try:
            with self.engine.connect() as conn:
                # 获取最后日期
                res = conn.execute(
                    text("SELECT MAX(date) FROM daily_k WHERE code = :code"),
                    {"code": code}
                )
                last_date = res.fetchone()[0]

                # 获取数据天数
                res_count = conn.execute(
                    text("SELECT COUNT(*) FROM daily_k WHERE code = :code"),
                    {"code": code}
                )
                data_count = res_count.fetchone()[0]

            today = datetime.now().date()

            # Range Filter 等长线 EMA 需要约 600 个交易日预热。
            # 当前回拉 1010 个自然日通常约覆盖 650+ 个交易日，避免日常同步反复触发历史回补。

            if last_date:
                # 检查数据天数是否足够
                if needs_history_backfill(data_count):
                    # 数据不足，需要补齐历史数据
                    needed_start_date = today - timedelta(days=HISTORY_LOOKBACK_CALENDAR_DAYS)
                    start_date = needed_start_date.strftime("%Y%m%d")
                    result["message"] = f"数据不足({data_count}天)，补齐历史数据..."
                elif last_date >= today:
                    # 数据足够且已是最新
                    result["success"] = True
                    result["message"] = f"已是最新 ({data_count}天)"
                    return result
                else:
                    # 数据足够但不是最新，增量更新
                    start_date = (last_date + timedelta(days=1)).strftime("%Y%m%d")
            else:
                # 无历史数据，获取足够的历史数据
                start_date = (today - timedelta(days=HISTORY_LOOKBACK_CALENDAR_DAYS)).strftime("%Y%m%d")

        except Exception as e:
            result["message"] = f"查询失败: {e}"
            return result

        # 尝试使用不同数据源
        sources_to_try = [s for s in self.manager.sources if s.is_available()]
        
        if not sources_to_try:
            result["message"] = "无可用数据源"
            return result

        for source in sources_to_try:
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

            except StockNotSupportedError as e:
                # 数据源确定不支持该股票（已退市/B股/代码无效等）：
                # 立即短路，不重试也不切换数据源——其它源大概率同样失败。
                logger.debug(f"{source.name} 确定不支持 {code}，跳过所有数据源: {e}")
                result["message"] = f"不支持该股票: {str(e)[:60]}"
                return result

            except Exception as e:
                err_msg = str(e)
                if any(err in err_msg for err in ["list index", "invalid literal", "KeyError", "not found"]):
                    logger.debug(f"{source.name} 无法解析或不支持 {code}: {err_msg}")
                else:
                    logger.warning(f"{source.name} 同步 {code} 失败: {err_msg}", exc_info=True)
                    source.record_failure(err_msg)

                # 短暂错峰后切换下个数据源（仅针对此支股票，不影响全局）。
                # 原固定 1s 对批量场景过重，改为轻微抖动。
                time.sleep(random.uniform(*SOURCE_FAILOVER_DELAY_RANGE))

        result["message"] = "所有的可用数据源均未返回数据"
        return result

    def sync_batch(self, codes: List[str], delay_range=(0.0, 0.0), progress_callback=None, max_workers=5, check_stop=None) -> Dict[str, Any]:
        """
        批量同步股票 (Enhanced with Threading)

        Args:
            codes: 股票代码列表
            delay_range: 主线程结果收集时的错峰间隔范围（秒）。默认 (0,0) 不延迟——
                工作线程本身已是并发，主线程不应在结果收集上人为 sleep 而拖慢吞吐。
            progress_callback: 进度回调函数 callback(current, total, success, failed)
            max_workers: 最大并发线程数
            check_stop: 检查是否应该停止的回调函数，返回 True 表示应该停止
        """
        results = {
            "total": len(codes),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "details": []
        }

        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        lock = threading.Lock()
        completed_count = 0
        stop_flag = False

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_code = {executor.submit(self.sync_single_stock, code): code for code in codes}

            for future in as_completed(future_to_code):
                # 每处理完一只股票就检查停止请求
                if not stop_flag:
                    if check_stop and check_stop():
                        logger.info("收到停止指令，中断同步...")
                        stop_flag = True

                if stop_flag:
                    # 取消剩余的任务
                    for f in future_to_code:
                        if not f.done():
                            f.cancel()
                    break

                code = future_to_code[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"success": False, "message": f"崩溃异常: {exc}", "source": "Unknown"}

                with lock:
                    completed_count += 1
                    results["details"].append(result)

                    if result.get("success"):
                        if "已是最新" in result.get("message", "") or "无新数据" in result.get("message", ""):
                            results["skipped"] += 1
                        else:
                            results["success"] += 1
                            logger.debug(f"[{completed_count}/{len(codes)}] {code}: {result['message']} (来源: {result.get('source')})")
                    else:
                        results["failed"] += 1
                        logger.warning(f"[{completed_count}/{len(codes)}] {code}: {result['message']}")

                    # 调用进度回调
                    if progress_callback:
                        if progress_callback(completed_count, len(codes), results["success"], results["failed"]) is False:
                            logger.info("收到停止指令，同步任务中断...")
                            stop_flag = True

                    if completed_count % 50 == 0:
                        logger.info(f"进度: {completed_count}/{len(codes)}, 成功: {results['success']}, "
                                  f"失败: {results['failed']}, 跳过: {results['skipped']}")

                if delay_range[1] > 0 and not stop_flag:
                    time.sleep(random.uniform(*delay_range))

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
