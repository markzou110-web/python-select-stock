# GitHub 同类系统调研与可纳入清单（2026-10-06）

调研对象：InStock（myhhub/stock，~7.7k star）、TradingAgents（TauricResearch，
AAAI 2025）、zvt、vnpy、KHunter。结论按"可纳入/观察/不纳入"分级。

## 一、可纳入（按优先级）

### P1：TradingAgents 式多空辩论 AI 复核

TradingAgents 的核心结构：分析师（基本面/情绪/新闻/技术）→ **多空研究员
辩论** → 交易员决策 → 风险团队辩论 → 执行。对本系统的落地：

- 现状：收盘 AI 复核是"单次总结"（analyze_strategy_candidates 一次调用）；
- 升级：对**每日 TOP3 候选**（不是全部，控成本）做 bull/bear 两角色辩论
  （bull 只看多证据、bear 只看空证据与风险桶，各自输出后由裁判合成结论），
  复用既有 `_post_chat_text` 客户端与 AI 配置；成本 2-3 倍调用 ×3 候选；
- 验证方式：两种模式各跑 4 周，对照候选 5 日实现收益（A/B 存档对比）；
- 落点：`core/ai_stock_analysis.py` 新增 debate 模式 + config 开关。

### P2：InStock 式轻量策略库与"策略模板"机制

InStock 内置放量上涨/停机坪/回踩年线/突破平台等策略 + 策略模板扩展 + 每
策略独立回测验证。对本系统的启示不是照抄策略（我们的核心策略族已有
walk-forward 纪律），而是**候选策略的流水线化管理**：

- 候选池：把"题材热度 TOP 主题的回踩/突破"等观察策略先入 shadow 池
  （研究雷达已有雏形），每个候选策略必须过三段 walk-forward 才能进证据门
  ——把本系统的纪律产品化；
- 落点：`core/strategy_registry.py` 增加 shadow 策略注册表 +
  `regime_attribution` 式验证脚手架复用。

### P3：K 线形态识别标注（InStock）

InStock 的 K 线形态识别（锤头/吞没/十字星等）作为图上标注层。本系统
K 线图已有信号 marker 体系（signalDisplay），加形态标注成本低、对盘后
复盘有参考价值。P3（纯展示，不进证据）。

## 二、观察（暂不纳入）

- **zvt 统一 schema**：架构优雅但迁移成本 >> 收益，本系统 db.py 单层已稳定；
- **筹码分布**（InStock 有）：SplitKLineCharts 已有筹码分布图，无需重复；
- **KHunter/vnpy**：交易框架方向，与本系统"研究工作站"定位不符。

## 三、不纳入

- 自动交易（InStock 有）：实盘自动执行与本系统"人工确认+证据门"的
  安全边界冲突；
- ML 因子挖掘（Azure-Tang/quant 式）：黑盒因子与"证据可解释"路线冲突。

## 附：进行中的验证

tv_dual_strict 配对窗口 3→5 的真实引擎回测（事件级近似已支持：
test 段 935 笔/+1.99%/PF1.48 vs 3 日窗 783 笔/+1.70%/PF1.41），
真实引擎结果落地后按 validation_gate 走常量变更。
