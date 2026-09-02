# Fund Holding X-Ray | 基金隐含风格透视

简体中文 | [English](README.en.md)

原作者：Roland（xxkyuss）；QUANTSKILLS 发布维护：abgyjaguo。社区项目，未声明官方认证或推荐。

一个基于 PandaAI 的 ETF / 基金持仓透视 Skill。输入基金代码，输出基金基础信息、Top 持仓、集中度、行业与主题暴露、风格判定、数据驱动洞察和风险提示。

A PandaAI-powered skill for ETF and fund holding analysis. Enter a fund symbol to generate basic fund information, top holdings, concentration metrics, sector and theme exposure, style classification, data-driven insights, and risk notes.

## 功能 / Features

| 中文 | English |
|:--|:--|
| 基金信息卡片：名称、类型、管理人、托管人、基准、指数属性、QDII 标记和状态 | Fund profile: name, type, manager, custodian, benchmark, index attributes, QDII flag, and status |
| Top 持仓：按估算权重排序并展示行业 | Top holdings ranked by estimated weight with sector labels |
| 集中度：Top1、Top3、CR5、CR10、HHI 和有效成分数 | Concentration: Top1, Top3, CR5, CR10, HHI, and effective number of holdings |
| 行业与主题暴露：数据源行业分类加权统计和关键词粗映射 | Sector and theme exposure using source classifications and keyword mapping |
| 风格判定：宽基、主题、跨境、商品、债券、Smart Beta、主动管理等 | Style classification: broad-market, thematic, cross-border, commodity, bond, Smart Beta, and active management |
| 三端输出：终端 rich 表格、ECharts HTML、归一化 JSON | Three outputs: rich terminal report, ECharts HTML, and normalized JSON |

## 数据接口 / Data APIs

| API | 用途 / Purpose | 地位 / Role |
|:--|:--|:--|
| `get_fund_detail` | 基金基础信息 / Fund profile | 任务书指定 / Required |
| `get_fund_etf_constituents` | ETF 申赎清单成分券 / ETF creation-redemption constituents | 任务书指定 / Required |
| `get_stock_daily` | 名称和收盘价，用于估算权重 / Names and closing prices for estimated weights | 补充 / Optional enrichment |
| `get_stock_detail` | 成分股行业分类 / Constituent sector classification | 补充 / Optional enrichment |

字段兼容和数据口径见 [references/api_reference.md](references/api_reference.md)。

See [references/api_reference.md](references/api_reference.md) for field compatibility rules and data conventions.

## 快速开始 / Quick Start

### 安装 / Install

```bash
python -m pip install -r requirements.txt
```

### 首次运行 / First run

```bash
python scripts/fund_xray.py --symbol 510300.SH
```

首次运行会交互式询问 PandaAI 账号和密码。密码不会回显，可选择保存到当前目录的 `.env`。`.env` 已被 `.gitignore` 忽略，不能上传到 GitHub。

On the first run, the script asks for PandaAI credentials interactively. The password is hidden while typing. You may save credentials to a local `.env` file. `.env` is ignored by `.gitignore` and must never be uploaded to GitHub.

### 常用命令 / Common commands

```bash
# 沪深 300 ETF / CSI 300 ETF
python scripts/fund_xray.py --symbol 510300.SH

# 红利 ETF，检查 Smart Beta 判定 / Dividend ETF, Smart Beta check
python scripts/fund_xray.py --symbol 510880.SH

# 纳指 ETF，检查跨境和汇率风险 / Nasdaq ETF, cross-border risk check
python scripts/fund_xray.py --symbol 513100.SH

# 指定日期和 Top 数量 / Specify date and number of holdings
python scripts/fund_xray.py --symbol 159915.SZ --date 20260717 --top 20

# 只使用两个任务指定接口 / Use only the two required APIs
python scripts/fund_xray.py --symbol 510300.SH --no-enrich

# 输出 JSON / Generate JSON
python scripts/fund_xray.py --symbol 510300.SH --json --save report.json

# 场外主动基金会自动降级 / OTC active funds use the fallback path
python scripts/fund_xray.py --symbol 110011.OF
```

## 输出 / Outputs

- **基金信息 / Fund profile**: `get_fund_detail` 返回的核心字段。
- **成分快照 / Constituent snapshot**: 最新成分日期、成分数量和必须现金替代数量。
- **Top 持仓 / Top holdings**: 默认展示前 10 只，可用 `--top` 调整。
- **集中度 / Concentration**: Top1、Top3、CR5、CR10、HHI 和有效成分数。
- **行业暴露 / Sector exposure**: 按估算权重聚合行业。
- **主题暴露 / Theme exposure**: 12 类关键词粗映射，仅供参考。
- **风格判定 / Style classification**: 使用指数属性、业绩基准、基金名称和成分信息综合判断，并输出证据链。
- **数据驱动洞察 / Data-driven insights**: 根据第一大持仓、集中度、行业、主题和现金替代情况生成。
- **风险提示 / Risk notes**: 输出时点滞后、权重估算、调仓、主题分类、集中度、跨境和现金替代等风险。

HTML 报告包含 Top10 条形图、行业权重环形图和洞察卡片。JSON 可供下游程序或前端使用。

The HTML report includes a Top10 bar chart, a sector exposure chart, and insight cards. JSON output can be consumed by downstream programs or front ends.

## 关键口径 / Methodology and Caveats

1. **权重不等于基金实际持仓 / Estimated weights are not official fund weights**：成分接口通常只提供数量，脚本使用 `quantity × closing price` 并按成分券口径归一化。绝对持仓市值还需要基金规模。
2. **被动、Smart Beta、主动管理双判定 / Two-signal style decision**：优先参考 `index_fund_type`，再结合业绩基准、跟踪指数和基金信息；不能只因为基准含指数就判定为被动。
3. **单日快照 / Single-date snapshot**：成分可能按季度或半年调整，跨调仓日的结果不能直接比较。
4. **主题是粗分类 / Theme labels are heuristic**：主题由关键词映射得到，仅用于快速观察，正式研究应核对指数编制方案和招募说明书。
5. **现金替代 / Cash substitution**：标志 `0=禁止`、`1=允许`、`2=必须`，报告重点统计必须现金替代。

## 项目结构 / Project Structure

```text
SKILL.md                         # Skill instructions / 技能规范
README.md                        # This bilingual README / 双语说明
LICENSE                          # GPL-3.0-only license
THIRD_PARTY_NOTICES.md           # Preserved original MIT notice
requirements.txt                # Python dependencies / Python 依赖
scripts/fund_xray.py             # Main CLI / 主脚本
scripts/test_fund_xray.py        # Offline and integration tests / 测试
references/api_reference.md      # API and schema notes / 接口参考
examples/                        # Sample HTML report and screenshot / 示例报告
```

## 测试 / Tests

```bash
cd scripts
python test_fund_xray.py
```

The test suite covers schema adaptation, date snapshots, weight estimation, concentration, style decisions, sector and theme exposure, risk notes, HTML rendering, JSON serialization, and end-to-end flows. Real API smoke tests run only when PandaAI credentials are configured.

测试覆盖字段兼容、日期快照、权重估算、集中度、风格判定、行业与主题暴露、风险提示、HTML、JSON 和端到端流程。只有配置 PandaAI 账号后，才会追加真实接口冒烟测试。

## 免责声明 / Disclaimer

本项目仅用于技术研究和学习交流，不构成投资建议。数据来自 PandaAI，权重为估算口径，与基金公告披露的正式持仓权重可能不同。

This project is for technical research and education only and does not constitute investment advice. Data is provided by PandaAI, and estimated weights may differ from official fund disclosures.

## License

[GPL-3.0-only](LICENSE)。原作者 Roland（xxkyuss）的 MIT 版权与许可声明完整保留在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目仅用于研究与教育，不构成投资建议。
