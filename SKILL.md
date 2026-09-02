---
name: skill-fund-holding-xray
description: 基于 PandaAI 数据，输入 ETF/基金代码即可输出基金隐含风格透视报告（基础信息/Top 持仓/集中度 CR5·CR10·HHI/行业主题暴露/风格粗分类/AI 数据洞察/风险提示），终端表格 + ECharts HTML 双输出。当用户需要透视 ETF 成分券、反推基金行业/主题/风格暴露、评估持仓集中度，或判定一只 ETF 是被动跟踪、Smart Beta 还是主动管理时使用。
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: quantskills/skill-fund-holding-xray
  repository_url: https://github.com/quantskills/skill-fund-holding-xray
  project_type: skill
  collection: fund-research
  license: GPL-3.0-only
  creator: Roland (xxkyuss)
  maintainer: abgyjaguo
  platforms: [claude-code, codex, cursor, hermes, openclaw]
---

# 基金隐含风格透视 (Fund Holding X-Ray)

输入一个 ETF / 基金代码，一键输出七段式报告：基金信息卡片 → Top 持仓 → 集中度 → 行业/主题暴露 → 风格判定 → AI 数据洞察 → 风险提示。终端 rich 表格 + Stripe 风格 HTML（ECharts 图表）。

数据源：PandaAI（`panda_data` SDK v0.0.12+）。任务书指定接口 + 实测补充接口：

| 接口 | 用途 | 地位 |
|:-----|:------|:------|
| `get_fund_detail` | 基金基础信息（46 字段：类型/基准/跟踪指数/指数属性/QDII 标记/状态） | 任务书指定 |
| `get_fund_etf_constituents` | ETF 申赎清单成分券（stock_symbol/quantity/现金替代标志） | 任务书指定 |
| `get_stock_daily` | 成分股名称 + 收盘价 → 估算权重（成分接口不返回权重/价格/成分名） | 补充，可关 |
| `get_stock_detail` | 成分股行业分类（sector_code_name）→ 行业暴露 | 补充，可关 |

字段口径与 schema 自适应规则见 [references/api_reference.md](references/api_reference.md)（已按 2026-07-18 真实接口实测校准）。

## 前置条件

```bash
pip install -r requirements.txt    # 或：pip install panda_data pandas rich jinja2
```

首次运行直接执行脚本，会交互式询问 PandaAI 账号密码（密码输入不回显）并保存到 `.env`，之后自动读取：

```bash
python scripts/fund_xray.py --symbol 510300.SH
```

## 命令用法

```bash
# 标准透视（默认最近交易日清单，向前回溯 15 天取最新，自动补充行情/行业）
python scripts/fund_xray.py --symbol 510300.SH
python scripts/fund_xray.py --symbol 510880.SH     # 红利 ETF → Smart Beta 判定

# 指定成分日期 / 回溯窗口 / Top 只数
python scripts/fund_xray.py --symbol 510300.SH --date 20260717 --lookback 15 --top 20

# 严格两接口模式（不调用行情/行业补充接口，权重降级为只数统计）
python scripts/fund_xray.py --symbol 510300.SH --no-enrich

# 归一化 JSON 输出（替代 HTML，供下游 Agent / 前端消费）
python scripts/fund_xray.py --symbol 510300.SH --json --save report.json
python scripts/fund_xray.py --symbol 510300.SH --json --no-html   # 打印到 stdout

# 只输出终端 / 自定义 HTML 路径
python scripts/fund_xray.py --symbol 510300.SH --no-html
python scripts/fund_xray.py --symbol 510300.SH --save ./report.html
```

非 ETF（如场外主动基金 `110011.OF`）自动降级：只输出基础信息 + 风格判定 + 风险提示。

## 输出内容

| 模块 | 内容 |
|:-----|:------|
| 🏦 **基金信息卡片** | get_fund_detail 全字段（长文本终端截断、HTML 完整展示） |
| 📦 **成分快照** | 成分日期、只数、必须现金替代数、权重口径说明 |
| 🏆 **Top N 持仓** | 按权重降序（默认 10），含行业列；权重 = quantity × 当日收盘价估算并归一化 |
| 📊 **集中度** | Top1 / Top3 / CR5 / CR10 / HHI 赫芬达尔指数 / 有效成分数（1/HHI）+ 定性解读 |
| 🏭 **行业暴露** | 数据源行业分类（sector_code_name）加权统计 |
| 🧭 **主题暴露** | 成分名称关键词 → 12 类主题（自建粗映射，仅供参考） |
| 🎨 **风格判定** | 基金粗分类（宽基/跨境/商品/债券/Smart Beta/行业主题）+ 被动 vs Smart Beta vs 主动双判定（附证据链） |
| 🤖 **AI 数据洞察** | 数据驱动自动生成（第一大权重/集中度/行业与主题暴露/综合判定/现金替代触发），无 AI API 依赖，非 ETF 自动降级 |
| ⚠️ **风险提示** | 固定口径提醒 + 触发型（高集中度/必须现金替代/跨境汇率/主动滞后/权重估算口径） |

HTML 报告含 ECharts Top10 条形图 + 行业权重环形图 + 渐变洞察卡片，文件命名 `fund_xray_{代码}_{时间戳}.html`。
`--json` 输出归一化 JSON（detail / snapshot / top_holdings / concentration / style / sector_exposure / theme_exposure / risk_notes / insights），替代 HTML，供下游 Agent 或前端直接消费。

## 内建的关键口径（任务书"关键坑"+ 实测修正，请勿绕过）

1. **权重 ≠ 绝对持仓**：成分权重是申赎篮子的相对权重（且为 数量×收盘价 估算值）；绝对持仓市值 = 权重 × 基金规模，规模需另行获取。报告固定输出该口径提醒。
2. **被动 vs Smart Beta vs 主动 双判定**：`index_fund_type`（I/EI/UN）为主、业绩基准/跟踪指数为佐证——主动基金基准也常含指数（实测 110011.OF 必须判"主动管理"），不单凭基准判被动；判定结论必附证据链。
3. **跨调仓日不可直接比**：成分按半年/季度定期调整 → 脚本只取**最新单日快照**，回溯窗口内的历史日期一律丢弃。
4. **主题分类主观**：关键词粗映射仅用于快速定位，输出处均标注"仅供参考"；行业暴露优先使用数据源行业分类。
5. **权重归一化**：申赎清单不含现金部分，权重按成分券口径归一化到 100% 并披露；跨境 ETF 境外成分覆盖不足时自动降级为只数统计。
6. **现金替代口径**：标志 0=禁止 / 1=允许 / 2=必须，只统计「必须」；flag=1 + 固定替代金额是跨市场 ETF 深市成分常态，不计入。

## 测试

```bash
cd scripts/
python test_fund_xray.py            # 116 断言：离线合成数据 + （有账号时）真实接口冒烟
# 配置 PANDA_DATA_USERNAME/PASSWORD（或 .env）后自动追加真实 API 集成测试
```

## 限制说明

- 仅支持单只基金逐只透视，不做全市场批量扫描（全市场分类请用 skill-fund-category-overview）。
- 权重为「数量 × 收盘价」估算值（成分接口不返回权重），与基金披露持仓权重存在口径差异。
- 主题/风格分类是关键词粗映射，不替代指数编制方案；正式研究请核对招募说明书。
- 成分接口不返回基金规模与净值，绝对持仓市值无法在本 skill 内闭环。
