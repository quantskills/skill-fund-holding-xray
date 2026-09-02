# PandaAI 基金接口参考（skill-fund-holding-xray）

> 字段口径已于 **2026-07-18 经真实接口实测校准**（账号实测 510300.SH / 510880.SH / 110011.OF）。

## 数据源与认证

| 项目 | 值 |
|:----|:----|
| 基 URL | `http://pandadata.pandaaiquant.com`（可用 `PANDA_DATA_BASE_URL` 覆盖） |
| SDK | `panda_data`（实测 v0.0.12，pip 安装） |
| 认证 | `panda_data.init_token(username, password, base_url)`，token 由 SDK 内部管理；账号存运行目录 `.env` |

## 接口 1：get_fund_detail —— 基金基础信息（任务书指定）

```python
df = panda_data.get_fund_detail(symbol="510300.SH")   # 实测返回 1 行 × 46 列
```

- 端点：`POST /fund/getFundDetailData`
- `symbol` 支持单只或列表，格式 `代码.市场`（`510300.SH` / `159919.SZ` / `000001.OF`）
- 过滤参数：`exchange`、`type`（E股票/H混合/B债券/M货币/O其他）、`etf_lof_type`（ETF/LOF/UN）、
  `index_fund_type`（I指数/EI指数增强/UN非指数）、`status`（L/S/DE/UN）、`fund_status`（A/E/UN）

**实测关键字段（46 列摘录）：**

| 字段 | 说明 |
|:-----|:------|
| `symbol` / `name` / `full_name` | 代码 / 简称 / 全称 |
| `type` / `operation_mode` / `etf_lof_type` | 类型码 / 运作模式 / ETF·LOF 标识 |
| `index_fund_type` | **I=指数型，EI=指数增强，UN=非指数**（被动判定信号 1） |
| `benchmark` / `index_symbol` / `index_name` | 业绩基准 / 跟踪指数（被动判定信号 2；注意可能为 NaN） |
| `is_qdii_fund` / `is_fof_fund` / `is_mom_fund` | 0/1 标记（QDII 直接定跨境风格） |
| `management` / `management_institution` | **基金经理（人名）** / 基金管理人（公司） |
| `custodian_institution` | 基金托管人 |
| `status` / `fund_status` / `found_date` / `listed_date` | 状态与日期 |
| `investment_field` / `strategy` / `philosophy` / `risk_return_profile` | 长文本（终端截断显示） |

## 接口 2：get_fund_etf_constituents —— ETF 申赎清单成分券（任务书指定）

```python
df = panda_data.get_fund_etf_constituents(
    start_date="20260702", end_date="20260717", symbol="510300.SH")
# 实测：11 个交易日 × 300 只 = 3300 行 × 11 列
```

- 端点：`POST /fund/getFundEtfConstituentsData`；日期区间最长 1 年
- **实践建议**：取最近 15 天窗口 → 客户端过滤到最新一个交易日，避免节假日空窗

**实测字段（11 列）：**

| 字段 | 说明 |
|:-----|:------|
| `symbol` / `date` | **基金代码** / 交易日期（前列固定） |
| `name` | ⚠️ **基金名称（整列同值），不是成分股名称！** 成分名称需补充接口 |
| `stock_symbol` | 成分券代码 |
| `quantity` | 每申赎单位成分股数（**接口不返回权重与价格**） |
| `cash_substitution_flag` | 现金替代标志：**0=禁止，1=允许，2=必须** |
| `cash_premium_rate` / `cash_discount_rate` | 现金替代溢价/折价比例（%） |
| `fixed_substitution_amount` | 固定替代金额（flag=1 允许替代的行也常带——跨市场 ETF 深市成分常态，**不要当作"必须替代"计数**） |
| `redemption_cash_amount` | 赎回现金金额 |

## 补充接口（解决实测 gap，可用 `--no-enrich` 关闭）

成分接口不返回权重、价格、成分名称，故默认追加两个补充接口：

| 接口 | 用途 |
|:-----|:------|
| `get_stock_daily(symbol=[...], start_date, end_date)` | 批量取成分股 `name` + `close`/`pre_close` → **权重 = quantity × 收盘价估算**，归一化到 100% |
| `get_stock_detail(symbol=[...])` | `sector_code_name` 数据源行业分类 → 行业暴露 |

估算权重与基金披露权重存在口径差异，报告固定披露；跨境 ETF 境外成分 A 股接口覆盖不足时自动降级为只数统计。

## schema 自适应（fund_xray.py `detect_columns`）

经典/实测两套字段名均可识别（先精确后子串匹配）：

| 概念 | 候选列名 |
|:-----|:---------|
| 成分代码 | `stock_symbol` `con_symbol` `constituent_symbol` `stock_code` `secu_code` … |
| 成分名称 | `stock_name` `con_name` `secu_name` …（裸 `name` 列整列同值时判为基金名，弃用） |
| 权重 | `weight` `ratio` `proportion` `pct` `component_weight` |
| 数量 | `quantity` `amount` `shares` `qty` `volume` |
| 现金替代 | `cash_substitution_flag` `sub_flag` `cash_flag` … |
| 固定替代金额 | `fixed_substitution_amount` `sub_amount` … |
| 日期 | `date` `trade_date` `ann_date` |

权重推算优先级：**接口权重列 > 数量×价格（快照内）> 行情补充估算 > 只数统计降级**。

## 口径备忘（任务书关键坑 + 实测修正）

1. 成分权重是**申赎篮子相对权重**，不等于实际持仓权重；绝对市值需 × 基金规模（接口不提供）。
2. **被动 vs 主动双判定**：`index_fund_type` 为主、`benchmark` 为佐证——主动基金基准也常含指数，
   实测 110011.OF（易方达优质精选，UN + 基准含指数）必须判"主动管理"，不能单凭基准判被动。
3. 成分按半年/季度调整，**只取单日快照**，跨调仓日数据不可比。
4. 主题/风格为自建关键词映射，主观分类差异大，仅供参考；行业暴露优先用数据源行业分类。
5. 现金替代只统计 flag=2「必须」；flag=1「允许」+固定替代金额是跨市场 ETF 深市成分常态。
6. 非场内 ETF 无申赎清单 → 自动降级为基础信息 + 风格判定 + 风险提示。
