"""
全市场历史数据回填脚本（v2 - 绕过 30s 超时，直调 impl）

问题背景：sync_single_stock 内部硬编码了 30s 超时（SYNC_SINGLE_STOCK_TIMEOUT）。
该超时对日常增量同步合理，但历史回填场景下，1500 日请求 + 24 路并发会让
腾讯限流排队，单股轻松超过 30s，导致雪崩式失败（实测 batch 2-3 失败率 ~50%）。

本脚本绕过 sync_single_stock 的超时包装，直接调用 _sync_single_stock_impl，
并降低并发到 8 worker（减轻腾讯压力），用 90s 硬超时（ThreadPoolExecutor 级别）
兜底防止单股卡死。

特性：
- 断点续传：每完成 CHECKPOINT_INTERVAL 只就持久化进度
- 进度监控：每批打印进度/速率/ETA
- 自定义超时：90s/股（vs 全局 30s），适合历史回填
- 自定义并发：8 worker（vs 全局 24），避免腾讯限流
- 失败重试：失败的股票在全部跑完后用单线程重试一次

运行方式：
    cd backend && source venv_new/bin/activate
    python scratch/backfill_history.py
"""
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.multi_source_sync import MultiSourceSync, needs_history_backfill
from core.db import get_db_engine
from core.logging_config import logger
from sqlalchemy import text

PROGRESS_FILE = "/tmp/backfill_progress.txt"
FAILED_FILE = "/tmp/backfill_failed.txt"
CODES_FILE = "/tmp/backfill_codes.txt"
CHECKPOINT_INTERVAL = 50
BACKFILL_WORKERS = 8        # 低于全局 24，避免腾讯限流
BACKFILL_TIMEOUT = 90       # 高于全局 30s，给历史回填留足时间


def load_codes():
    with open(CODES_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def load_checkpoint():
    done = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                code = line.strip()
                if code:
                    done.add(code)
    return done


def append_checkpoint(codes):
    with open(PROGRESS_FILE, "a") as f:
        for c in codes:
            f.write(c + "\n")
        f.flush()


def _sync_with_timeout(syncer, code, timeout):
    """直调 _sync_single_stock_impl，外加自定义超时兜底。"""
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(syncer._sync_single_stock_impl, code)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning(f"{code} 回填超时（>{timeout}s），跳过")
            return {"code": code, "success": False, "message": f"回填超时（>{timeout}s）", "source": None}


def run_batch(syncer, codes, workers, timeout):
    """并发执行一批股票的回填，返回 details 列表。"""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_code = {executor.submit(_sync_with_timeout, syncer, c, timeout): c for c in codes}
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"code": code, "success": False, "message": f"崩溃: {exc}", "source": "Unknown"})
    return results


def main():
    all_codes = load_codes()
    done = load_checkpoint()
    pending = [c for c in all_codes if c not in done]

    logger.info(f"=== 全市场历史回填 v2 启动 ===")
    logger.info(f"总数: {len(all_codes)}  已完成: {len(done)}  待处理: {len(pending)}")
    logger.info(f"并发: {BACKFILL_WORKERS} workers  超时: {BACKFILL_TIMEOUT}s/股")
    if not pending:
        logger.info("全部已完成，无待处理股票")
        return

    syncer = MultiSourceSync()
    engine = get_db_engine()

    code_days_before = {}
    with engine.connect() as conn:
        r = conn.execute(text("SELECT code, COUNT(*) FROM daily_k GROUP BY code")).fetchall()
        for code, cnt in r:
            code_days_before[code] = cnt

    success = 0
    failed = 0
    skipped = 0
    failed_list = []
    batch_buffer = []
    t0 = time.time()

    for batch_start in range(0, len(pending), CHECKPOINT_INTERVAL):
        batch = pending[batch_start:batch_start + CHECKPOINT_INTERVAL]
        details = run_batch(syncer, batch, BACKFILL_WORKERS, BACKFILL_TIMEOUT)

        for detail in details:
            code = detail.get("code")
            if detail.get("success"):
                if "已是最新" in detail.get("message", "") or "无新数据" in detail.get("message", ""):
                    skipped += 1
                else:
                    success += 1
            else:
                failed += 1
                failed_list.append((code, detail.get("message", "")[:80]))
            if code:
                batch_buffer.append(code)

        append_checkpoint(batch_buffer)
        batch_buffer = []

        elapsed = time.time() - t0
        processed = success + failed + skipped
        rate = processed / elapsed if elapsed > 0 else 0
        remaining = (len(pending) - processed) / rate if rate > 0 else 0
        logger.info(
            f"[{processed}/{len(pending)}] "
            f"成功 {success}  跳过 {skipped}  失败 {failed}  "
            f"速率 {rate:.1f} 只/秒  "
            f"预计剩余 {remaining/60:.0f} 分钟"
        )

    # 失败重试（单线程，更稳定）
    if failed_list:
        logger.info(f"=== 失败重试（{len(failed_list)} 只，单线程）===")
        retry_success = 0
        retry_codes = [c for c, _ in failed_list]
        failed_list = []  # 清空，重新记录真正失败的
        for code in retry_codes:
            detail = _sync_with_timeout(syncer, code, BACKFILL_TIMEOUT * 2)
            if detail.get("success"):
                retry_success += 1
                success += 1
                failed -= 1
            else:
                failed_list.append((code, detail.get("message", "")[:80]))
        logger.info(f"重试成功: {retry_success}/{len(retry_codes)}")

    if failed_list:
        with open(FAILED_FILE, "w") as f:
            for code, msg in failed_list:
                f.write(f"{code}\t{msg}\n")
        logger.info(f"最终失败列表已写入 {FAILED_FILE}（共 {len(failed_list)} 只）")

    # 统计回填后深度
    total_after = 0
    converged = 0
    with engine.connect() as conn:
        r = conn.execute(text("SELECT code, COUNT(*) FROM daily_k GROUP BY code")).fetchall()
        for code, cnt in r:
            total_after += cnt
            if not needs_history_backfill(cnt):
                converged += 1

    total_before = sum(code_days_before.values())
    logger.info(f"=== 回填完成 ===")
    logger.info(f"总耗时: {(time.time()-t0)/60:.1f} 分钟")
    logger.info(f"成功: {success}  跳过: {skipped}  失败: {failed}")
    logger.info(f"daily_k 总行数: {total_before:,} → {total_after:,}  (净增 {total_after-total_before:,})")
    logger.info(f"已收敛（≥950天）: {converged} / {len(all_codes)} ({converged*100/len(all_codes):.1f}%)")


if __name__ == "__main__":
    main()
