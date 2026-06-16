import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from core.multi_source_sync import (
    MIN_REQUIRED_TRADING_DAYS,
    needs_history_backfill,
    SYNC_MAX_WORKERS,
    SYNC_SINGLE_STOCK_TIMEOUT,
    SOURCE_FAILOVER_DELAY_RANGE,
    StockNotSupportedError,
    MultiSourceSync,
    TushareDataSource,
    TencentDataSource,
)


def test_needs_history_backfill_uses_trading_day_threshold():
    assert needs_history_backfill(MIN_REQUIRED_TRADING_DAYS - 1)
    assert not needs_history_backfill(MIN_REQUIRED_TRADING_DAYS)
    assert not needs_history_backfill(665)


# ---------------------------------------------------------------------------
# 测试辅助：构造一个无需真实 DB / 网络的 MultiSourceSync 实例。
# ---------------------------------------------------------------------------

def _make_syncer(sources, engine_mock=None, engine=None):
    """构造一个注入 mock manager 与 mock engine 的 MultiSourceSync。

    MultiSourceSync.__init__ 默认会 new DataSourceManager()（触发网络探测）并
    get_db_engine()。这里通过传入预构造 manager + 直接覆写 engine 绕开。
    engine 是 engine_mock 的别名，方便用关键字传参。
    """
    manager = MagicMock()
    manager.sources = sources
    syncer = MultiSourceSync.__new__(MultiSourceSync)
    syncer.manager = manager
    syncer.engine = engine or engine_mock or MagicMock()
    return syncer


def _conn_returning(last_date, data_count):
    """构造一个 mock engine.connect() 上下文，返回指定 MAX(date)/COUNT(*) 结果。"""
    res_max = MagicMock()
    res_max.fetchone.return_value = (last_date,)
    res_count = MagicMock()
    res_count.fetchone.return_value = (data_count,)

    conn = MagicMock()
    # 两次 execute 分别返回 MAX(date) 与 COUNT(*)
    conn.execute.side_effect = [res_max, res_count]

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)

    engine = MagicMock()
    engine.connect.return_value = ctx
    return engine


class _FakeSource:
    """可编程的假数据源，便于在测试中模拟成功/失败/不支持等行为。

    get_hist_data 委托给 self._get_hist（默认 MagicMock），因此调用次数可追踪，
    也可通过设置 self._get_hist.side_effect / return_value 编程行为。
    """

    def __init__(self, name, hist_data=None, exc=None, priority=0):
        self.name = name
        self.priority = priority
        self.record_success = MagicMock()
        self.record_failure = MagicMock()
        self._get_hist = MagicMock()
        if exc is not None:
            self._get_hist.side_effect = exc
        else:
            self._get_hist.return_value = hist_data

    def is_available(self):
        return True

    def get_hist_data(self, code, start_date):
        return self._get_hist(code, start_date)


# ---------------------------------------------------------------------------
# 1. StockNotSupportedError 短路：不重试、不切换下一个数据源
# ---------------------------------------------------------------------------

def test_sync_single_stock_skips_not_supported(monkeypatch):
    """源抛 StockNotSupportedError 时应立即返回失败，不调用后续数据源。"""
    bad_source = _FakeSource("腾讯财经", exc=StockNotSupportedError("不支持 999999"))
    next_source = _FakeSource("Tushare", hist_data=__import__("pandas").DataFrame())
    syncer = _make_syncer([bad_source, next_source], engine=_conn_returning(None, 0))

    # 直接调用 impl，绕过外层超时包装器以隔离被测逻辑。
    result = syncer._sync_single_stock_impl("999999")

    assert result["success"] is False
    assert "不支持" in result["message"]
    # 关键断言：后续数据源的 get_hist_data 不应被调用（短路，不切换数据源）
    next_source._get_hist.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 腾讯源退避：5 次失败的总 sleep 时长 < 2s（验证指数退避收敛）
# ---------------------------------------------------------------------------

def test_tencent_retry_backoff_shorter(monkeypatch):
    sleeps = []
    monkeypatch.setattr(
        "core.multi_source_sync.time.sleep", lambda s: sleeps.append(s)
    )
    src = TencentDataSource()
    with patch("core.multi_source_sync.ak.stock_zh_a_hist_tx", side_effect=RuntimeError("连接超时")):
        try:
            src.get_hist_data("000001", "20240101")
        except RuntimeError:
            pass  # 第 5 次失败会 raise

    # 5 次尝试之间只有 4 次 sleep
    assert len(sleeps) == 4
    # 指数退避 0.2→0.3→0.45→0.68，加上抖动(<=0.1)，总和应远小于原来的 ~6s
    assert sum(sleeps) < 2.0, f"退避总睡眠过长: {sum(sleeps)}s, sleeps={sleeps}"


# ---------------------------------------------------------------------------
# 3. Tushare 令牌桶：并发 N 次调用的总耗时应 < N × _min_interval
#    （证明锁不在 sleep 期间持有，线程得以交错执行）
# ---------------------------------------------------------------------------

def test_tushare_token_bucket_allows_interleave():
    import threading

    # 重置类级状态，避免受其它测试影响
    TushareDataSource._last_call_time = 0.0

    src = TushareDataSource.__new__(TushareDataSource)
    src.priority = 1
    # 不需要真实 token/pro，_reserve_slot 是纯类级时间戳逻辑
    src.pro = None
    src.token = None

    N = 5
    threads = []
    start_times = []
    lock = threading.Lock()

    def worker():
        wait = src._reserve_slot()
        if wait > 0:
            time.sleep(wait)
        with lock:
            start_times.append(time.time())

    for _ in range(N):
        threads.append(threading.Thread(target=worker))
    t0 = time.time()
    for t in threads:
        t.start()
        # 微小启动错开，确保预约顺序确定
        time.sleep(0.001)
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    # 若锁在 sleep 期间持有（旧实现），N 个调用将串行 → 耗时 ≈ N × 1.3s。
    # 令牌桶允许交错预约，最后一个 slot 约在 (N-1)×1.3s 处就绪，
    # 但总耗时应明显小于 N × _min_interval（放宽到 0.9× 作为安全阈值）。
    serial_bound = N * TushareDataSource._min_interval
    assert elapsed < 0.9 * serial_bound, (
        f"令牌桶未生效：{N} 次调用耗时 {elapsed:.2f}s，"
        f"接近串行上界 {serial_bound:.2f}s"
    )


# ---------------------------------------------------------------------------
# 4. sync_batch 尊重传入的 max_workers
# ---------------------------------------------------------------------------

def test_sync_batch_max_workers_config():
    """sync_batch 应使用传入的 max_workers，而非默认值。"""
    captured = {}

    real_init = MultiSourceSync.__init__

    class FakeExecutor:
        def __init__(self, max_workers=None):
            captured["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, *args):
            f = MagicMock()
            f.result.return_value = {"success": True, "message": "已是最新"}
            return f

    import concurrent.futures as cf

    syncer = _make_syncer([], engine=MagicMock())

    with patch("concurrent.futures.ThreadPoolExecutor", FakeExecutor):
        # 替换 as_completed，使其对空 future 集合直接返回
        with patch("concurrent.futures.as_completed", lambda x: iter(x)):
            syncer.sync_batch(["000001"], max_workers=SYNC_MAX_WORKERS)

    assert captured["max_workers"] == SYNC_MAX_WORKERS


# ---------------------------------------------------------------------------
# 5. 回归保护：已是最新数据时不发起任何网络调用
# ---------------------------------------------------------------------------

def test_sync_single_stock_already_latest_returns_early():
    """数据足够且 last_date >= today 时应立即返回，不调用任何源的 get_hist_data。"""
    today = datetime.now().date()
    # data_count=700 (>650 阈值) 且 last_date >= today → 命中"已是最新"分支
    engine = _conn_returning(today, 700)

    network_source = _FakeSource("腾讯财经", hist_data=None)  # 不应被调用
    syncer = _make_syncer([network_source], engine=engine)

    result = syncer._sync_single_stock_impl("000001")

    assert result["success"] is True
    assert "已是最新" in result["message"]
    # 关键回归断言：不应触达任何数据源的网络调用
    network_source._get_hist.assert_not_called()
