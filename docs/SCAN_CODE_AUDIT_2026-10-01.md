# 扫描代码审查与修复（2026-10-01）

## 范围

审核扫描六阶段及跨阶段上下文、扫描 API/Celery 任务契约、结果持久化、任务审计、前端轮询和历史加载。使用标准库符号表检查 backend/core 与 backend/routers 顶层 Python 模块的未定义全局引用；运行后端全量测试、前端单元测试、类型检查和 ESLint。此检查不覆盖所有条件分支中的局部变量初始化，也不构成全部策略收益与安全性的证明。

## 已修复的确定问题

| 问题 | 修复及验证 |
| --- | --- |
| 数据装载缺少 candidates 上下文赋值 | 从 ctx.candidates 读取，覆盖正常及空候选路径 |
| 评估结束日志引用未定义 start_time | 从 ctx.start_time 读取；覆盖匹配与无匹配评估 |
| 参数寻优缺少 validate_stock_code 导入 | 补既有校验函数导入；合法/非法代码端点回归 |
| 无候选跳过完成阶段，保留旧结果 | 初始空候选与流动性过滤后空候选走统一持久化、候选刷新及 scan_end |
| 历史数据缺失的 HTTP 404 被包装成 500 | 独立重抛 HTTPException |
| 保存失败仍报告成功或更新推送候选 | 检查 save_scan_results 返回值；保存成功后才更新 Sentinel；失败写 FAILED 审计 |
| Celery 可能丢失 HTTPException 的错误说明 | 任务边界转换为携带 status/detail 的 RuntimeError；真实 JSON 异常往返回归 |
| 调度任务返回 error 却审计 SUCCESS | status=error 映射 FAILURE；保留 error/detail/reason |
| 测试用 except Exception: pass 掩盖运行错误 | 改为断言明确 HTTP 503；新增全局未定义引用检查 |
| 前端 REVOKED 继续扫描、慢轮询重复弹窗、历史刷新中断跟踪 | 终止状态处理、轮询互斥及停止保护、独立历史请求代数；五项实际 store 回归 |

## 验证

- 后端全量测试：1,247 项通过；后续增加的完成阶段及消息 fallback 测试另经相关 69 项测试验证通过。
- 前端：24 项测试通过；TypeScript 检查通过；改动的前端文件 ESLint 通过。
- 全局符号检查通过；新增前端扫描测试已接入现有 CI。
- 本地全市场实际扫描：读取 2026-09-30 数据，初筛 4,359 只，评估 54 个形态匹配，后续筛选及决策得到 46 条研究观察结果；153.11 秒完成并写入 PostgreSQL。该策略没有授予交易权限。
- 服务重载后再次从真实 API 提交至 Celery 扫描队列：任务 693e6ad7-3e0a-40fd-9486-3a32f3268698 用时 145.12 秒，状态 SUCCESS；API 返回 46 条，数据库对应策略记录 46 条，任务审计 SUCCESS。
- API 健康检查正常；参数寻优非法代码返回 HTTP 400；四个工作队列均响应 pong。
- 修改经 Python、任务/API及前端交叉审查。

## 尚需单独处理的审查项

1. 单股任务循环在 as_completed 后调用 future.result(timeout=60)，不能限制仍未完成的工作；macOS 的 Celery solo 池不提供硬超时。后续应针对数据接口超时和整段截止时间设计失败收尾，避免错误宣称线程已终止。
2. 取消接口立即报告 REVOKED，但 macOS solo 池执行中不能及时处理取消/强制终止。前端终止状态已修复，后台取消仍需要协作式取消或支持终止的进程池；尚未改变任务池。
3. 全仓 ESLint 既有 76 个错误、20 个警告。现有 CI 将此项设为非阻塞；本次没有做全仓格式或类型重构。
4. 后端测试有第三方弃用和已有异步测试警告；本次测试均通过，未升级依赖。

## 变更文件

- backend/core/scanner.py：阶段变量、空结果、持久化顺序与失败处理。
- backend/routers/scan.py：校验导入及异常序列化边界。
- backend/core/celery_app.py：任务失败审计和摘要。
- frontend/src/stores/scanStore.ts：扫描状态跟踪与历史请求隔离。
- backend/tests/test_scanner_data_loading.py、test_scanner_completion.py、test_scanner_strategy_paths.py、test_scan_failure_contract.py、test_task_failure_audit.py、test_undefined_runtime_names.py：回归及静态防护。
- frontend/tests/scanStore.test.mjs、.github/workflows/ci.yml：前端回归及 CI。
