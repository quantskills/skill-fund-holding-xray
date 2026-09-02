#!/usr/bin/env python3
"""
skill-fund-holding-xray — 基金隐含风格透视

输入 ETF / 基金代码，调用 PandaAI 数据接口：
  - get_fund_detail            基金基础信息（任务书指定）
  - get_fund_etf_constituents  ETF 申赎清单成分券（任务书指定）
  - get_stock_daily            成分股名称/收盘价（补充接口，用于估算权重）
  - get_stock_detail           成分股行业分类（补充接口，用于行业暴露）

输出：基金信息卡片 + Top 持仓 + 集中度（CR5/CR10/HHI）+ 行业/主题暴露 +
      风格粗分类 + AI 数据洞察 + 风险提示，终端 rich 表格 + ECharts HTML 报告。

首次使用会交互式询问 PandaAI 账号密码并保存到 .env 文件。

实测口径（2026-07-18 真实接口验证）：
  - 成分券接口返回 quantity（每申赎单位股数），不返回权重/价格/成分名称；
    权重 = quantity × 当日收盘价（get_stock_daily）估算，并在口径中注明。
  - 成分接口的 name 列是基金名称（每行相同），成分名称来自 get_stock_daily。
  - cash_substitution_flag：0=禁止 1=允许 2=必须现金替代。

任务书关键坑（已内建到分析逻辑）：
  1. ETF 成分是权重 ≠ 绝对持仓，绝对市值须乘基金规模；
  2. 被动跟踪与主动 Smart Beta 用 index_fund_type + benchmark 双判定；
  3. 成分调整节奏半年 / 季度，跨调仓日不可直接比 → 只取单日快照；
  4. 主题 ETF 主观分类差异大 → 自建关键词映射，结果标注"主观映射，仅供参考"。
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

# ── 依赖检查 ──────────────────────────────────────────────

MISSING = []

try:
    import panda_data
except ImportError:
    MISSING.append("panda_data")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
except ImportError:
    MISSING.append("rich")

try:
    from jinja2 import Template
except ImportError:
    MISSING.append("jinja2")

if MISSING:
    print(f"缺少依赖包，请安装：pip install {' '.join(MISSING)}", file=sys.stderr)
    sys.exit(1)

console = Console()

PANDA_BASE_URL = "http://pandadata.pandaaiquant.com"

# ── 登录（与 fund-category-overview 模板同构）─────────────────


def _env_path() -> Path:
    return Path.cwd() / ".env"


def _load_env():
    env_file = _env_path()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _save_env(username: str, password: str):
    env_file = _env_path()
    existing = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    existing["PANDA_DATA_USERNAME"] = username
    existing["PANDA_DATA_PASSWORD"] = password
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8"
    )
    console.print(f"[green]✅ 账号已保存到 {env_file}[/green]")


def _interactive_login() -> tuple[str, str]:
    console.print("\n[yellow]🔑 未检测到登录信息，请输入 PandaAI 账号：[/yellow]")
    username = input("  账号: ").strip()
    password = getpass.getpass("  密码: ").strip()
    if not username or not password:
        console.print("[red]账号和密码不能为空[/red]")
        sys.exit(1)
    save = input("  保存到 .env 文件？(y/n, 默认 y): ").strip().lower()
    if save != "n":
        _save_env(username, password)
    return username, password


def pandata_login():
    """登录 PandaAI（panda_data v0.0.12：token 由 SDK 内部管理）。"""
    _load_env()
    username = os.environ.get("PANDA_DATA_USERNAME")
    password = os.environ.get("PANDA_DATA_PASSWORD")
    base_url = os.environ.get("PANDA_DATA_BASE_URL", PANDA_BASE_URL)
    if not username or not password:
        username, password = _interactive_login()
    panda_data.init_token(username=username, password=password, base_url=base_url)


# ── 数据层适配器 ────────────────────────────────────────────
# 任务书指定接口：get_fund_detail / get_fund_etf_constituents
# 补充接口（用于补全成分名称与估算权重）：get_stock_daily / get_stock_detail


def fetch_fund_detail(symbol: str) -> pd.DataFrame:
    """任务书接口 1：get_fund_detail —— 基金基础信息。"""
    df = panda_data.get_fund_detail(symbol=symbol)
    return df if df is not None else pd.DataFrame()


def fetch_etf_constituents(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """任务书接口 2：get_fund_etf_constituents —— ETF 申赎清单成分券。"""
    df = panda_data.get_fund_etf_constituents(
        start_date=start_date, end_date=end_date, symbol=symbol
    )
    return df if df is not None else pd.DataFrame()


def fetch_stock_quotes(symbols: list[str], date: str) -> pd.DataFrame:
    """补充接口：get_stock_daily —— 成分股名称与收盘价（单次批量）。"""
    df = panda_data.get_stock_daily(symbol=symbols, start_date=date, end_date=date)
    return df if df is not None else pd.DataFrame()


def fetch_stock_sectors(symbols: list[str]) -> pd.DataFrame:
    """补充接口：get_stock_detail —— 成分股行业分类（sector_code_name）。"""
    df = panda_data.get_stock_detail(symbol=symbols)
    return df if df is not None else pd.DataFrame()


def latest_trade_date() -> str:
    """最近交易日（YYYYMMDD）。失败则回退为今天。"""
    try:
        d = panda_data.get_last_trade_date()
        if isinstance(d, (list, tuple, pd.Series)):
            d = d[-1] if not isinstance(d, pd.Series) else d.iloc[-1]
        return str(d).replace("-", "")[:8]
    except Exception:
        return datetime.now().strftime("%Y%m%d")


# ── 成分券 schema 自适应（字段口径已按真实接口校准）────────────────

COLUMN_CANDIDATES = {
    "code": ["stock_symbol", "con_symbol", "constituent_symbol", "stock_code",
             "secu_code", "underlying_symbol", "component_code", "ts_code", "con_code"],
    "name": ["stock_name", "con_name", "secu_name", "component_name",
             "stock_short_name", "name"],
    "weight": ["weight", "ratio", "proportion", "pct", "weighting",
               "component_weight", "stk_mkv_ratio", "weights"],
    "shares": ["quantity", "amount", "shares", "qty", "volume", "num",
               "component_shares", "con_amount"],
    "price": ["price", "close", "last_price", "px", "close_price"],
    "sub_flag": ["cash_substitution_flag", "sub_flag", "cash_flag",
                 "substitute_flag", "replace_flag", "cash_substitute_sign"],
    "sub_amount": ["fixed_substitution_amount", "sub_amount",
                   "cash_substitute_amount", "replace_amount", "substitute_amount"],
    "date": ["date", "trade_date", "ann_date", "declare_date"],
}

# 现金替代标志（实测：0=禁止 1=允许 2=必须）
SUB_FLAG_LABELS = {"0": "禁止", "1": "允许", "2": "必须"}


def detect_columns(df: pd.DataFrame) -> dict[str, Optional[str]]:
    """把概念字段映射到实际列名：先精确匹配，再子串匹配。"""
    lower = {c.lower(): c for c in df.columns}
    mapping: dict[str, Optional[str]] = {}
    for concept, candidates in COLUMN_CANDIDATES.items():
        hit = None
        for cand in candidates:
            if cand in lower:
                hit = lower[cand]
                break
        if hit is None:  # 子串兜底
            for cand in candidates:
                for lc, orig in lower.items():
                    if cand in lc:
                        hit = orig
                        break
                if hit:
                    break
        mapping[concept] = hit
    return mapping


@dataclass
class ConstituentSnapshot:
    """单日成分券快照（跨调仓日不可直接比 → 只保留一个日期）。"""
    date: str
    df: pd.DataFrame          # code/name/weight/shares/price/sector/sub_flag
    weight_source: str        # 权重口径说明
    n_cash_sub: int = 0       # 必须现金替代成分数
    weight_estimated: bool = False   # 权重是否为 数量×收盘价 估算
    raw_columns: list = field(default_factory=list)


def build_snapshot(raw: pd.DataFrame) -> Optional[ConstituentSnapshot]:
    """原始成分券 DataFrame → 单日标准化快照。空数据返回 None。"""
    if raw is None or raw.empty:
        return None
    cols = detect_columns(raw)

    # 1. 只取最新一个交易日（任务坑 3：跨调仓日不可直接比）
    date_col = cols.get("date")
    if date_col:
        dates = sorted(raw[date_col].astype(str).str.replace("-", "").str[:8].unique())
        latest = dates[-1]
        d = raw[raw[date_col].astype(str).str.replace("-", "").str[:8] == latest].copy()
    else:
        latest = "未知日期"
        d = raw.copy()

    out = pd.DataFrame()
    out["code"] = (d[cols["code"]].astype(str) if cols.get("code")
                   else [f"#{i+1}" for i in range(len(d))])

    # 名称：实测成分接口的 name 列是基金名称（整列同值）→ 不能用，置为代码待补充
    if cols.get("name") and d[cols["name"]].nunique(dropna=True) > 1:
        out["name"] = d[cols["name"]].astype(str)
    else:
        out["name"] = out["code"]

    # 2. 权重：接口权重列 > 数量×价格（快照内）> 待后续行情补充
    weight_source = ""
    if cols.get("weight"):
        w = pd.to_numeric(d[cols["weight"]], errors="coerce")
        if w.dropna().max() <= 1.5:
            w = w * 100
        out["weight_raw"] = w
        weight_source = f"接口权重字段 `{cols['weight']}`"
    elif cols.get("shares") and cols.get("price"):
        mkv = (pd.to_numeric(d[cols["shares"]], errors="coerce")
               * pd.to_numeric(d[cols["price"]], errors="coerce"))
        total = mkv.sum()
        out["weight_raw"] = mkv / total * 100 if total > 0 else pd.NA
        weight_source = f"由 `{cols['shares']}`×`{cols['price']}` 估算"
    else:
        out["weight_raw"] = pd.NA
        weight_source = "成分接口未返回权重/价格，权重待行情接口补充估算"

    out["shares"] = (pd.to_numeric(d[cols["shares"]], errors="coerce")
                     if cols.get("shares") else pd.NA)
    out["price"] = pd.NA
    out["sector"] = ""

    # 3. 现金替代：数值标志 0=禁止 1=允许 2=必须；只统计「必须」
    #    （flag=1 允许替代的行也常带固定替代金额——跨市场 ETF 深市成分常态，不计入）
    n_sub = 0
    if cols.get("sub_flag"):
        flag = d[cols["sub_flag"]].astype(str).str.strip()
        out["sub_flag"] = flag.map(lambda x: SUB_FLAG_LABELS.get(x, x))
        n_sub = int(flag.isin(["2"]).sum() + flag.str.contains("必须", na=False).sum())
    else:
        out["sub_flag"] = ""

    # 4. 若快照内已有权重 → 归一化；否则留给行情补充步骤
    w = out["weight_raw"]
    if w.notna().any() and w.sum() > 0:
        out["weight"] = (w / w.sum() * 100).round(4)
        weight_source += f"；按成分券口径归一化（原始合计 {w.sum():.1f}%）"
        estimated = "估算" in weight_source
    else:
        out["weight"] = pd.NA
        estimated = False

    out = out.sort_values("weight", ascending=False, na_position="last").reset_index(drop=True)
    return ConstituentSnapshot(
        date=latest, df=out, weight_source=weight_source,
        n_cash_sub=n_sub, weight_estimated=estimated, raw_columns=list(raw.columns),
    )


# ── 行情/行业补充（解决实测 gap：无权重、无成分名称）─────────────────


def enrich_snapshot(snap: ConstituentSnapshot, *, with_quotes: bool = True,
                    with_sectors: bool = True) -> ConstituentSnapshot:
    """用补充接口补全成分名称/行业，并用 数量×收盘价 估算权重。"""
    codes = [c for c in snap.df["code"].astype(str).tolist() if not c.startswith("#")]
    if not codes:
        return snap

    if with_quotes:
        try:
            q = fetch_stock_quotes(codes, snap.date)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[dim]行情补充失败（{exc}），按无权重降级[/dim]")
            q = pd.DataFrame()
        if not q.empty and "symbol" in q.columns:
            q = q.rename(columns={"symbol": "code"})
            q["code"] = q["code"].astype(str)
            if "pre_close" in q.columns:
                q["price"] = pd.to_numeric(q["close"], errors="coerce").fillna(
                    pd.to_numeric(q["pre_close"], errors="coerce"))
            else:
                q["price"] = pd.to_numeric(q["close"], errors="coerce")
            keep = [c for c in ["code", "name", "price"] if c in q.columns]
            # 快照自带的空 price 列先丢弃，避免合并时被后缀顶掉
            df = snap.df.drop(columns=["price"], errors="ignore").merge(
                q[keep], on="code", how="left", suffixes=("", "_q"))
            if "name_q" in df.columns:
                need = df["name"].isna() | (df["name"] == df["code"])
                df.loc[need, "name"] = df.loc[need, "name_q"]
                df = df.drop(columns=["name_q"])
            df["price"] = pd.to_numeric(df.get("price"), errors="coerce")
            covered = int(df["price"].notna().sum())
            if snap.df["weight"].notna().any():
                pass  # 已有接口权重，不覆盖
            elif covered >= max(1, int(0.5 * len(df))) and df["shares"].notna().any():
                mkv = df["shares"] * df["price"]
                total = mkv.sum()
                if total > 0:
                    df["weight"] = (mkv / total * 100).round(4)
                    snap.weight_estimated = True
                    snap.weight_source = (
                        f"权重 = 成分数量(quantity) × {snap.date} 收盘价估算"
                        f"（补充接口 get_stock_daily，覆盖 {covered}/{len(df)} 只）；"
                        "按成分券口径归一化")
                    df = df.sort_values("weight", ascending=False,
                                        na_position="last").reset_index(drop=True)
            else:
                snap.weight_source += (f"；行情覆盖不足（{covered}/{len(df)}，"
                                       "可能为跨境 ETF 或大面积停牌），权重降级为只数统计")
            snap.df = df

    if with_sectors:
        try:
            s = fetch_stock_sectors(codes)
        except Exception:  # noqa: BLE001
            s = pd.DataFrame()
        if not s.empty and {"symbol", "sector_code_name"}.issubset(s.columns):
            sec = s.rename(columns={"symbol": "code"})[["code", "sector_code_name"]]
            sec["code"] = sec["code"].astype(str)
            snap.df = snap.df.merge(sec, on="code", how="left")
            snap.df["sector"] = snap.df["sector_code_name"].fillna("")
            snap.df = snap.df.drop(columns=["sector_code_name"])

    return snap


# ── 集中度分析 ──────────────────────────────────────────────


@dataclass
class Concentration:
    n: int
    top1: Optional[float]
    top3: Optional[float]
    cr5: Optional[float]
    cr10: Optional[float]
    hhi: Optional[float]          # 赫芬达尔指数（0~1）
    eff_n: Optional[float]        # 有效成分数 = 1/HHI
    level: str                    # 集中度定性解读


def compute_concentration(snap: ConstituentSnapshot) -> Concentration:
    w = snap.df["weight"].dropna()
    n = len(snap.df)
    if w.empty:
        return Concentration(n=n, top1=None, top3=None, cr5=None, cr10=None,
                             hhi=None, eff_n=None, level="无权重数据，集中度指标不可用")
    top1 = round(float(w.head(1).sum()), 2)
    top3 = round(float(w.head(3).sum()), 2)
    cr5 = round(float(w.head(5).sum()), 2)
    cr10 = round(float(w.head(10).sum()), 2)
    hhi = float(((w / 100) ** 2).sum())
    eff_n = round(1 / hhi, 1) if hhi > 0 else None
    if cr10 >= 60 or hhi >= 0.18:
        level = "高集中——头部成分主导，波动易受权重股牵引"
    elif cr10 >= 35 or hhi >= 0.08:
        level = "中等集中——权重分布较均衡，仍有头部效应"
    else:
        level = "低集中——分散度高，接近广泛复制指数"
    return Concentration(n=n, top1=top1, top3=top3, cr5=cr5, cr10=cr10,
                         hhi=round(hhi, 4), eff_n=eff_n, level=level)


# ── 行业 / 主题 / 风格粗分类 ─────────────────────────────────

# 基金层面：按 基金名称 + 业绩基准 + 跟踪指数 关键词判定（顺序即优先级）
FUND_STYLE_RULES: list[tuple[str, list[str]]] = [
    ("跨境/QDII", ["纳斯达克", "标普", "道琼", "日经", "德国", "法国", "恒生", "港股",
                   "H股", "海外", "全球", "QDII", "沙特", "东南亚", "美国", "日本",
                   "印度", "越南", "亚太"]),
    ("商品", ["黄金", "白银", "豆粕", "原油", "商品", "饲料", "能源化工", "有色"]),
    ("债券", ["债", "利率", "信用", "国债", "城投", "可转债"]),
    ("货币", ["货币", "现金", "理财"]),
    ("Smart Beta/因子", ["红利", "低波", "质量", "价值", "成长", "动量", "等权",
                        "基本面", "自由现金流", "ESG", "龙头", "增强", "高股息",
                        "央企", "国企", "科创创业"]),
    ("宽基", ["沪深300", "中证500", "中证1000", "上证50", "创业板", "科创50",
              "中证A500", "中证2000", "中证100", "中证800", "上证综指", "深证成指",
              "北证50", "科创100", "A50", "A500", "300", "500", "1000"]),
]

# 成分股层面：名称关键词 → 主题（粗粒度，仅供参考）
STOCK_THEME_KEYWORDS: dict[str, list[str]] = {
    "半导体/芯片": ["半导体", "芯片", "集成电路", "晶圆", "中芯", "韦尔", "兆易",
                    "卓胜微", "圣邦", "澜起", "寒武纪", "海光", "中微", "北方华创"],
    "医药/医疗": ["医药", "医疗", "生物", "药业", "制药", "医院", "迈瑞", "恒瑞", "药明"],
    "新能源": ["新能源", "锂电", "光伏", "储能", "宁德", "比亚迪", "隆基", "阳光电源",
               "亿纬", "天齐", "赣锋", "通威"],
    "金融/银行": ["银行", "证券", "保险", "金融", "券商"],
    "消费/白酒": ["消费", "食品", "饮料", "白酒", "茅台", "五粮液", "乳", "家电", "美的", "格力"],
    "军工/国防": ["军工", "国防", "航空", "航天", "船舶", "兵器"],
    "AI/算力/软件": ["智能", "算力", "软件", "数据", "云计算", "科大讯飞", "金山", "用友", "浪潮"],
    "通信/电子": ["通信", "电子", "5G", "光学", "面板", "立讯", "歌尔", "京东方", "中兴"],
    "有色/资源": ["有色", "铜", "铝", "黄金", "稀土", "煤", "矿", "钢铁", "石油", "石化", "化工", "紫金"],
    "地产/基建": ["地产", "建筑", "基建", "建材", "水泥", "万科", "保利"],
    "汽车": ["汽车", "车辆", "长城", "长安", "上汽"],
    "农业": ["农业", "养殖", "种植", "牧原", "温氏"],
}


def sector_exposure(snap: ConstituentSnapshot, top: int = 10) -> pd.DataFrame:
    """行业暴露：基于数据源行业分类（get_stock_detail.sector_code_name）。"""
    df = snap.df
    if "sector" not in df.columns or df["sector"].astype(str).str.strip().eq("").all():
        return pd.DataFrame()
    has_w = df["weight"].notna().any()
    rows = []
    for sec, grp in df[df["sector"].astype(str).str.strip() != ""].groupby("sector"):
        wsum = float(grp["weight"].sum()) if has_w else None
        rows.append({"行业": sec, "成分数": len(grp),
                     "权重(%)": round(wsum, 2) if wsum is not None else None})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values("权重(%)" if has_w else "成分数",
                          ascending=False, na_position="last")
    out.attrs["has_weight"] = bool(has_w)
    return out.head(top).reset_index(drop=True)


def theme_exposure(snap: ConstituentSnapshot) -> pd.DataFrame:
    """成分股名称关键词 → 主题权重暴露（自建粗映射）。"""
    rows = []
    df = snap.df
    has_w = df["weight"].notna().any()
    matched_w = 0.0
    for theme, keywords in STOCK_THEME_KEYWORDS.items():
        mask = df["name"].astype(str).apply(
            lambda s: any(kw.lower() in s.lower() for kw in keywords))
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        wsum = float(df.loc[mask, "weight"].sum()) if has_w else None
        if wsum:
            matched_w += wsum
        rows.append({"主题": theme, "成分数": cnt,
                     "权重(%)": round(wsum, 2) if wsum is not None else None})
    if not rows:
        out = pd.DataFrame(columns=["主题", "成分数", "权重(%)"])
    else:
        out = pd.DataFrame(rows).sort_values(
            "权重(%)" if has_w else "成分数", ascending=False, na_position="last")
    out.attrs["matched_weight"] = round(matched_w, 2)
    out.attrs["has_weight"] = bool(has_w)
    return out.reset_index(drop=True)


def classify_fund_style(name: str, benchmark: str, index_name: str = "",
                        is_qdii: bool = False) -> tuple[str, str]:
    """基金层面风格粗分类 → (分类, 命中依据)。"""
    if is_qdii:
        return "跨境/QDII", "is_qdii_fund=1（数据源标记）"
    text = f"{name} {benchmark} {index_name}"
    for style, keywords in FUND_STYLE_RULES:
        for kw in keywords:
            if kw.lower() in text.lower():
                return style, f"名称/基准/跟踪指数命中关键词「{kw}」"
    return "行业/主题（未识别）", "未命中宽基/跨境/商品/因子关键词，默认按行业主题处理"


def classify_passive(name: str, benchmark: str, index_fund_type: str,
                     index_name: str = "") -> tuple[str, list[str]]:
    """被动跟踪 vs Smart Beta 双判定（任务坑 2：fund_type + benchmark 双判定）。

    index_fund_type: I=指数型, EI=指数增强型, UN=非指数基金
    """
    evidences = []
    idx_code = str(index_fund_type).strip().upper()
    idx_flag = {"I": "指数型", "EI": "指数增强型", "UN": "非指数基金"}.get(idx_code, "")
    evidences.append(f"index_fund_type={index_fund_type or '缺失'}"
                     + (f"（{idx_flag}）" if idx_flag else "（无法识别）"))

    bench_has_index = "指数" in benchmark or bool(index_name.strip())
    evidences.append(f"业绩基准/跟踪指数{'含' if bench_has_index else '不含'}指数信息"
                     + (f"（{index_name}）" if index_name.strip() else ""))

    sb_hits = [kw for kw in FUND_STYLE_RULES[4][1]
               if kw.lower() in f"{name} {benchmark} {index_name}".lower()]
    if sb_hits:
        evidences.append(f"命中 Smart Beta 关键词：{'、'.join(sb_hits[:3])}")

    if idx_code == "EI":
        verdict = "指数增强（主动 + 指数混合）"
    elif idx_code == "I" and sb_hits:
        verdict = "Smart Beta / 因子型被动"
    elif idx_code == "I":
        verdict = "被动指数跟踪"
    elif idx_code == "UN":
        verdict = ("主动管理（index_fund_type=非指数"
                   + ("，以指数为业绩比较基准" if bench_has_index else "") + "）")
        evidences.append("注意：主动基金的基准含指数 ≠ 被动跟踪")
    elif bench_has_index:
        verdict = "指数相关产品（index_fund_type 缺失，属性待确认）"
    else:
        verdict = "疑似主动管理（非典型被动 ETF）"
    return verdict, evidences


# ── 风险提示 ──────────────────────────────────────────────


def build_risks(symbol: str, style: str, verdict: str,
                snap: Optional[ConstituentSnapshot],
                conc: Optional[Concentration], is_qdii: bool = False) -> list[str]:
    """把任务书关键坑 + 数据发现转成风险提示。"""
    risks = [
        "口径提醒：申赎清单成分权重 ≠ 基金实际持仓权重；"
        "如需绝对持仓市值，须用权重 × 基金最新规模（本接口不含规模字段）。",
        "调仓提醒：指数成分按半年/季度定期调整，本报告为单日快照，"
        "跨调仓日的成分数据不可直接比较。",
        "分类提醒：主题/风格为关键词粗映射，主观分类存在差异，仅供快速参考，"
        "正式研究请核对指数编制方案与招募说明书。",
    ]
    if snap is None:
        risks.insert(0, "未取到申赎清单成分：该基金可能不是场内 ETF，"
                        "或当日未公布清单；成分/集中度/暴露分析已跳过。")
        return risks
    if snap.weight_estimated:
        risks.append("权重为「成分数量 × 当日收盘价」估算值（成分接口不返回权重），"
                     "与基金公司披露的实际持仓权重存在口径差异。")
    if conc and conc.cr10 is not None and conc.cr10 >= 60:
        risks.append(f"集中度风险：前十大成分权重 {conc.cr10}%，"
                     "头部权重股波动将显著传导至基金净值。")
    if snap.n_cash_sub > 0:
        risks.append(f"现金替代：{snap.n_cash_sub} 只成分为「必须现金替代」，"
                     "申赎与跟踪可能存在偏差（停牌/涨跌停成分常见）。")
    if style == "跨境/QDII" or is_qdii:
        risks.append("跨境 ETF：注意汇率波动、境外交易时差、外汇额度与"
                     "长期溢价风险；A 股行情接口无法覆盖境外成分，权重估算可能缺失。")
    if "增强" in verdict or "主动" in verdict:
        risks.append("该基金含主动管理成分，成分清单可能滞后于实际调仓，"
                     "透视结果置信度下降。")
    return risks


# ── AI 数据洞察（数据驱动，每条都由实际数据触发，不写硬编码结论）────────


def generate_insights(symbol: str, fund_row: dict,
                      snap: Optional[ConstituentSnapshot],
                      conc: Optional[Concentration], style: str, verdict: str,
                      sec_exp: pd.DataFrame, theme_exp: pd.DataFrame) -> list[str]:
    """基于透视结果自动生成洞察段落（无 AI API 依赖）。"""
    insights: list[str] = []

    if snap is None:
        insights.append("未取到申赎清单成分（可能非场内 ETF 或当日未公布清单），"
                        "持仓/集中度/暴露维度洞察不可用，本次仅输出基础信息与风格判定。")
        insights.append(f"综合判定：{style} · {verdict}。")
        return insights

    w = snap.df["weight"].dropna()
    if not w.empty:
        top_row = snap.df.iloc[0]
        top1_w = float(top_row["weight"])
        insights.append(
            f"第一大权重股 {top_row['name']}（{top_row['code']}）权重 {top1_w:.2f}%"
            + ("，单票波动对基金净值影响显著。" if top1_w >= 15 else "。"))
    if conc is not None and conc.cr10 is not None:
        insights.append(
            f"前十大成分合计权重 {conc.cr10}%，HHI {conc.hhi}，"
            f"有效成分数约 {conc.eff_n} 只。{conc.level}。")
    if sec_exp is not None and not sec_exp.empty and sec_exp.attrs.get("has_weight"):
        r0 = sec_exp.iloc[0]
        if pd.notna(r0["权重(%)"]):
            top3_w = float(sec_exp["权重(%)"].head(3).sum())
            insights.append(
                f"行业暴露以「{r0['行业']}」为首（{float(r0['权重(%)']):.2f}%），"
                f"前三大行业合计 {top3_w:.2f}%"
                + ("，行业集中度偏高。" if top3_w >= 60 else "，行业分布相对均衡。"))
    if theme_exp is not None and not theme_exp.empty and theme_exp.attrs.get("has_weight"):
        r0 = theme_exp.iloc[0]
        if pd.notna(r0["权重(%)"]):
            matched = theme_exp.attrs.get("matched_weight", 0)
            insights.append(
                f"主题暴露以「{r0['主题']}」为主（{float(r0['权重(%)']):.2f}%）；"
                f"关键词粗映射共覆盖 {matched}% 权重，未覆盖部分无法归类。")
    insights.append(f"综合判定：{style} · {verdict}。")
    if snap.n_cash_sub > 0:
        insights.append(f"{snap.n_cash_sub} 只成分为「必须现金替代」，"
                        "申赎与指数跟踪可能存在偏差（停牌/涨跌停成分常见）。")
    return insights


# ── 终端输出（rich）────────────────────────────────────────

DETAIL_FIELD_LABELS = {
    "symbol": "基金代码", "name": "基金名称", "trade_name": "场内简称",
    "full_name": "基金全称", "exchange": "交易市场", "type": "基金类型",
    "operation_mode": "运作模式", "index_fund_type": "指数型属性",
    "etf_lof_type": "ETF/LOF", "status": "上市状态", "fund_status": "基金状态",
    "is_qdii_fund": "QDII", "is_fof_fund": "FOF", "is_mom_fund": "MOM",
    "is_guaranteed_fund": "保本", "found_date": "成立日期", "listed_date": "上市日期",
    "delisted_date": "退市日期", "due_date": "到期日期",
    "management": "基金经理", "management_institution": "基金管理人",
    "management_short_name": "管理人简称", "custodian_institution": "基金托管人",
    "custodian_short_name": "托管人简称", "benchmark": "业绩比较基准",
    "index_symbol": "跟踪指数代码", "index_name": "跟踪指数",
    "is_class_fund": "分级基金", "class_name": "分级名称",
    "investment_objective": "投资目标", "investment_field": "投资范围",
    "strategy": "投资策略", "philosophy": "投资理念", "mode": "模式",
    "dividend_policy": "分红政策", "risk_return_profile": "风险收益特征",
    "guarantee_period": "保本期", "guarantee_ratio": "保本比例",
    "circulating_shares": "流通份额", "clearing_speed": "清算速度",
    "fund_type": "基金类型", "invest_type": "投资类型",
    "m_fee": "管理费率(%)", "c_fee": "托管费率(%)", "custodian": "基金托管人",
}

CARD_VALUE_MAXLEN = 100  # 终端卡片单值最大长度，超出截断


def print_fund_card(detail: pd.DataFrame, symbol: str) -> dict:
    """打印基金信息卡片，返回首行 dict（供后续分类用）。"""
    if detail.empty:
        console.print(f"[red]未查询到基金 {symbol} 的基础信息[/red]")
        return {}
    row = detail.iloc[0].to_dict()
    t = Table(title=f"🏦 {row.get('name', symbol)}（{symbol}）", box=box.ROUNDED,
              show_header=False, title_justify="left")
    t.add_column("字段", style="cyan", no_wrap=True)
    t.add_column("值", style="white", overflow="fold")
    for k, v in row.items():
        if pd.isna(v) or str(v) == "":
            continue
        label = DETAIL_FIELD_LABELS.get(str(k), str(k))
        text = str(v).replace("\n", " ")
        if len(text) > CARD_VALUE_MAXLEN:
            text = text[:CARD_VALUE_MAXLEN] + "…"
        t.add_row(label, text)
    console.print(t)
    return row


def print_snapshot_info(snap: ConstituentSnapshot):
    console.print(Panel(
        f"成分日期：[bold]{snap.date}[/bold]    成分只数：[bold]{len(snap.df)}[/bold]    "
        f"必须现金替代：[bold]{snap.n_cash_sub}[/bold]\n权重口径：{snap.weight_source}",
        title="📦 成分快照", border_style="blue"))


def print_top_holdings(snap: ConstituentSnapshot, top: int):
    t = Table(title=f"🏆 Top {top} 成分券", box=box.ROUNDED, title_justify="left")
    t.add_column("#", justify="right", style="dim")
    t.add_column("代码", style="cyan")
    t.add_column("名称", style="white")
    t.add_column("行业", style="blue")
    t.add_column("权重(%)", justify="right", style="green")
    t.add_column("现金替代", style="yellow")
    for i, r in snap.df.head(top).iterrows():
        w = f"{r['weight']:.2f}" if pd.notna(r["weight"]) else "—"
        sector = str(r.get("sector") or "") or "—"
        t.add_row(str(i + 1), str(r["code"]), str(r["name"]), sector, w,
                  str(r["sub_flag"] or ""))
    console.print(t)


def print_concentration(conc: Concentration):
    if conc.cr5 is None:
        console.print(Panel(conc.level, title="📊 集中度", border_style="yellow"))
        return
    console.print(Panel(
        f"Top1         [bold green]{conc.top1}%[/bold green]    "
        f"Top3         [bold green]{conc.top3}%[/bold green]\n"
        f"CR5（前五）  [bold green]{conc.cr5}%[/bold green]    "
        f"CR10（前十） [bold green]{conc.cr10}%[/bold green]\n"
        f"HHI          [bold green]{conc.hhi}[/bold green]    "
        f"有效成分数   [bold green]{conc.eff_n}[/bold green]\n\n{conc.level}",
        title="📊 集中度", border_style="magenta"))


def print_style(style: str, style_hit: str, verdict: str, evidences: list[str]):
    console.print(Panel(
        f"基金风格粗分类：[bold cyan]{style}[/bold cyan]（{style_hit}）\n"
        f"被动/主动判定 ：[bold cyan]{verdict}[/bold cyan]\n"
        + "\n".join(f"  · {e}" for e in evidences),
        title="🎨 风格判定", border_style="green"))


def _print_exposure(exp: pd.DataFrame, key_col: str, title: str):
    if exp is None or exp.empty:
        console.print(f"[dim]{title}：无数据，跳过。[/dim]")
        return
    has_w = exp.attrs.get("has_weight")
    t = Table(title=title, box=box.ROUNDED, title_justify="left")
    t.add_column(key_col, style="cyan")
    t.add_column("成分数", justify="right")
    if has_w:
        t.add_column("权重(%)", justify="right", style="green")
    for _, r in exp.iterrows():
        row = [str(r[key_col]), str(r["成分数"])]
        if has_w:
            row.append(f"{r['权重(%)']:.2f}" if pd.notna(r["权重(%)"]) else "—")
        t.add_row(*row)
    console.print(t)


def print_sector_exposure(sec: pd.DataFrame):
    _print_exposure(sec, "行业", "🏭 行业暴露（数据源行业分类）")


def print_theme_exposure(exp: pd.DataFrame):
    _print_exposure(exp, "主题", "🧭 主题暴露（粗映射）")
    if exp is not None and not exp.empty and exp.attrs.get("has_weight"):
        console.print(f"[dim]已匹配主题合计权重 {exp.attrs.get('matched_weight')}%，"
                      "未匹配部分为映射未覆盖成分；粗映射仅供参考。[/dim]")


def print_risks(risks: list[str]):
    body = "\n".join(f"[yellow]{i}.[/yellow] {r}" for i, r in enumerate(risks, 1))
    console.print(Panel(body, title="⚠️ 风险提示", border_style="red"))


def print_insights(insights: list[str]):
    if not insights:
        return
    body = "\n".join(f"[magenta]→[/magenta] {ins}" for ins in insights)
    console.print(Panel(body, title="🤖 AI 数据洞察", border_style="magenta"))


# ── HTML 报告（Stripe 风格，对齐 fund-category-overview 模板）──────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
         background:#f6f9fc; color:#0a2540; padding:32px 16px; }
  .wrap { max-width:1080px; margin:0 auto; }
  h1 { font-size:26px; font-weight:700; }
  .sub { color:#425466; font-size:13px; margin-top:6px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
           gap:16px; margin:24px 0; }
  .card { background:#fff; border-radius:16px; padding:20px;
          box-shadow:0 2px 8px rgba(10,37,64,.06); }
  .card .v { font-size:26px; font-weight:700; color:#533afd; font-variant-numeric:tabular-nums; }
  .card .k { font-size:12px; color:#425466; margin-top:4px; }
  .section { background:#fff; border-radius:16px; padding:24px; margin-bottom:20px;
             box-shadow:0 2px 8px rgba(10,37,64,.06); }
  .section h2 { font-size:17px; margin-bottom:14px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:#425466; font-weight:600; padding:8px 10px;
       border-bottom:2px solid #e6ebf1; }
  td { padding:8px 10px; border-bottom:1px solid #eef2f7; font-variant-numeric:tabular-nums;
       word-break:break-all; }
  tr:hover td { background:#f6f9fc; }
  .num { text-align:right; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
  @media (max-width:760px){ .grid2{ grid-template-columns:1fr; } }
  .chart { width:100%; height:340px; }
  .info td:first-child { color:#425466; width:150px; }
  .risk li { font-size:13px; color:#7a2e0e; background:#fff7ed;
             border:1px solid #fed7aa; border-radius:10px; padding:10px 14px;
             margin-bottom:8px; list-style:none; }
  .tag { display:inline-block; background:#ede9fe; color:#533afd; border-radius:999px;
         padding:3px 12px; font-size:12px; font-weight:600; margin-right:8px; }
  .foot { color:#8792a2; font-size:12px; text-align:center; margin-top:28px; }
  .muted { color:#8792a2; font-size:13px; }
  .insights { background:linear-gradient(135deg,#533afd 0%,#7c5cff 100%);
              border-radius:16px; padding:24px 28px; margin-bottom:20px; color:#fff;
              box-shadow:0 4px 20px rgba(83,58,253,.25); }
  .insights h2 { font-size:15px; margin-bottom:12px; opacity:.85; letter-spacing:.02em; }
  .insights ul { list-style:none; }
  .insights li { font-size:13px; line-height:1.7; padding:3px 0; opacity:.95; }
  .insights li::before { content:"→ "; opacity:.65; }
</style>
</head>
<body><div class="wrap">
  <h1>🔍 {{ fund_name }}</h1>
  <div class="sub">{{ symbol }} · 成分日期 {{ snap_date }} · 数据来源 PandaAI（get_fund_detail / get_fund_etf_constituents）· 生成 {{ gen_time }}</div>

  <div class="cards">
    <div class="card"><div class="v">{{ n_cons }}</div><div class="k">成分券只数</div></div>
    <div class="card"><div class="v">{{ top1 }}</div><div class="k">Top1 权重(%)</div></div>
    <div class="card"><div class="v">{{ top3 }}</div><div class="k">Top3 权重(%)</div></div>
    <div class="card"><div class="v">{{ cr5 }}</div><div class="k">Top5 权重(%)</div></div>
    <div class="card"><div class="v">{{ cr10 }}</div><div class="k">Top10 权重(%)</div></div>
    <div class="card"><div class="v">{{ hhi }}</div><div class="k">HHI 赫芬达尔</div></div>
    <div class="card"><div class="v">{{ eff_n }}</div><div class="k">有效成分数</div></div>
  </div>

  {% if insights %}
  <div class="insights"><h2>🤖 AI 数据洞察</h2>
    <ul>{% for ins in insights %}<li>{{ ins }}</li>{% endfor %}</ul>
  </div>
  {% endif %}

  <div class="section"><h2>🏦 基金基础信息</h2>
    <table class="info">{% for k, v in info_rows %}<tr><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table>
  </div>

  <div class="section"><h2>🎨 风格判定</h2>
    <p><span class="tag">{{ style }}</span><span class="tag">{{ verdict }}</span></p>
    <table>{% for e in evidences %}<tr><td>· {{ e }}</td></tr>{% endfor %}
    <tr><td>分类依据：{{ style_hit }}</td></tr></table>
  </div>

  {% if has_cons %}
  <div class="grid2">
    <div class="section"><h2>🏆 Top {{ top_n }} 成分券</h2>
      <table><tr><th>#</th><th>代码</th><th>名称</th><th>行业</th><th class="num">权重(%)</th></tr>
      {% for r in top_rows %}<tr><td>{{ loop.index }}</td><td>{{ r.code }}</td><td>{{ r.name }}</td><td>{{ r.sector }}</td><td class="num">{{ r.weight }}</td></tr>{% endfor %}
      </table>
    </div>
    <div class="section"><h2>📊 Top 10 权重</h2><div id="barTop" class="chart"></div></div>
  </div>

  <div class="grid2">
    <div class="section"><h2>🏭 行业暴露（数据源行业分类）</h2>
      {% if sector_rows %}
      <table><tr><th>行业</th><th class="num">成分数</th>{% if has_weight %}<th class="num">权重(%)</th>{% endif %}</tr>
      {% for r in sector_rows %}<tr><td>{{ r.name }}</td><td class="num">{{ r.count }}</td>{% if has_weight %}<td class="num">{{ r.weight }}</td>{% endif %}</tr>{% endfor %}
      </table>
      {% else %}<p class="muted">行业分类数据不可用。</p>{% endif %}
    </div>
    <div class="section"><h2>🥧 行业权重分布</h2><div id="pieSector" class="chart"></div></div>
  </div>

  <div class="section"><h2>🧭 主题暴露（自建关键词映射，仅供参考）</h2>
    {% if theme_rows %}
    <table><tr><th>主题</th><th class="num">成分数</th>{% if has_weight %}<th class="num">权重(%)</th>{% endif %}</tr>
    {% for r in theme_rows %}<tr><td>{{ r.name }}</td><td class="num">{{ r.count }}</td>{% if has_weight %}<td class="num">{{ r.weight }}</td>{% endif %}</tr>{% endfor %}
    </table>
    {% else %}<p class="muted">成分名称未命中主题关键词。</p>{% endif %}
  </div>
  {% else %}
  <div class="section"><h2>📦 成分数据</h2>
    <p class="muted">未取到申赎清单成分：该基金可能不是场内 ETF，或当日未公布清单。</p>
  </div>
  {% endif %}

  <div class="section"><h2>⚠️ 风险提示</h2>
    <ul class="risk">{% for r in risks %}<li>{{ r }}</li>{% endfor %}</ul>
  </div>

  <div class="foot">skill-fund-holding-xray · 基金隐含风格透视 · 口径：{{ weight_source }}</div>
</div>
{% if has_cons %}
<script>
const INDIGO = ['#533afd','#7c6cf8','#a394fb','#c6bdfc','#3d2fd6','#2f23a8','#8f85e8','#5e51e0'];
const bar = echarts.init(document.getElementById('barTop'));
bar.setOption({ grid:{left:8,right:24,top:16,bottom:8,containLabel:true},
  xAxis:{type:'value',axisLabel:{formatter:'{value}%'}},
  yAxis:{type:'category',data:{{ bar_names | safe }},inverse:true},
  series:[{type:'bar',data:{{ bar_weights | safe }},itemStyle:{color:'#533afd',borderRadius:[0,6,6,0]},
           label:{show:true,position:'right',formatter:'{c}%'}}] });
const pie = echarts.init(document.getElementById('pieSector'));
pie.setOption({ color:INDIGO, tooltip:{trigger:'item',formatter:'{b}: {c}% ({d}%)'},
  series:[{type:'pie',radius:['42%','70%'],itemStyle:{borderRadius:8,borderColor:'#fff',borderWidth:2},
           label:{formatter:'{b}\\n{c}%'},data:{{ pie_data | safe }}}] });
window.addEventListener('resize',()=>{bar.resize();pie.resize();});
</script>
{% endif %}
</body></html>
"""


def render_html(path: Path, *, symbol: str, fund_row: dict, snap: Optional[ConstituentSnapshot],
                conc: Optional[Concentration], style: str, style_hit: str,
                verdict: str, evidences: list[str], sec_exp: pd.DataFrame,
                theme_exp: pd.DataFrame, risks: list[str], top: int,
                insights: Optional[list[str]] = None):
    info_rows = []
    for k, v in (fund_row or {}).items():
        if pd.isna(v) or str(v) == "":
            continue
        info_rows.append((DETAIL_FIELD_LABELS.get(str(k), str(k)), str(v)))

    has_cons = snap is not None
    has_w = bool(has_cons and snap.df["weight"].notna().any())
    top_rows, bar_names, bar_weights = [], [], []
    sector_rows, theme_rows, pie_data = [], [], []
    if has_cons:
        for _, r in snap.df.head(top).iterrows():
            w = f"{r['weight']:.2f}" if pd.notna(r["weight"]) else "—"
            top_rows.append({"code": r["code"], "name": r["name"],
                             "sector": str(r.get("sector") or "") or "—", "weight": w})
        for _, r in snap.df.head(10).iterrows():
            if pd.notna(r["weight"]):
                bar_names.append(f'"{r["name"]}"')
                bar_weights.append(f"{r['weight']:.2f}")
        if sec_exp is not None and not sec_exp.empty:
            for _, r in sec_exp.iterrows():
                sector_rows.append({
                    "name": r["行业"], "count": r["成分数"],
                    "weight": f"{r['权重(%)']:.2f}" if pd.notna(r["权重(%)"]) else "—"})
                if has_w and pd.notna(r["权重(%)"]) and r["权重(%)"] > 0:
                    pie_data.append(f'{{"name":"{r["行业"]}","value":{r["权重(%)"]:.2f}}}')
        if theme_exp is not None and not theme_exp.empty:
            for _, r in theme_exp.iterrows():
                theme_rows.append({
                    "name": r["主题"], "count": r["成分数"],
                    "weight": f"{r['权重(%)']:.2f}" if pd.notna(r["权重(%)"]) else "—"})

    html = Template(HTML_TEMPLATE).render(
        title=f"基金隐含风格透视 {symbol}",
        fund_name=(fund_row or {}).get("name", symbol), symbol=symbol,
        snap_date=snap.date if snap else "—",
        gen_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        n_cons=conc.n if conc else 0,
        top1=conc.top1 if conc and conc.top1 is not None else "—",
        top3=conc.top3 if conc and conc.top3 is not None else "—",
        cr5=conc.cr5 if conc and conc.cr5 is not None else "—",
        cr10=conc.cr10 if conc and conc.cr10 is not None else "—",
        hhi=conc.hhi if conc and conc.hhi is not None else "—",
        eff_n=conc.eff_n if conc and conc.eff_n is not None else "—",
        info_rows=info_rows, style=style, style_hit=style_hit,
        verdict=verdict, evidences=evidences,
        has_cons=has_cons, top_n=top, top_rows=top_rows,
        sector_rows=sector_rows, theme_rows=theme_rows, has_weight=has_w,
        risks=risks, weight_source=snap.weight_source if snap else "—",
        insights=insights or [],
        bar_names="[" + ",".join(bar_names) + "]",
        bar_weights="[" + ",".join(bar_weights) + "]",
        pie_data="[" + ",".join(pie_data) + "]",
    )
    path.write_text(html, encoding="utf-8")


# ── JSON 归一化输出（对接下游 Agent / 前端）────────────────────


def json_safe(value):
    """NaN/NA → None；numpy 标量 → Python 原生类型；递归处理 dict/list。"""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        import numpy as np
        if isinstance(value, np.generic):
            return value.item()
    except ImportError:
        pass
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def build_payload(symbol: str, fund_row: dict, snap: Optional[ConstituentSnapshot],
                  conc: Optional[Concentration], style: str, style_hit: str,
                  verdict: str, evidences: list[str], sec_exp: pd.DataFrame,
                  theme_exp: pd.DataFrame, risks: list[str], top: int,
                  insights: Optional[list[str]] = None) -> dict:
    """归一化 JSON 负载：与终端/HTML 同源，供下游 Agent / 前端消费。"""
    payload: dict = {
        "symbol": symbol,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "detail": {DETAIL_FIELD_LABELS.get(str(k), str(k)): v
                   for k, v in (fund_row or {}).items()},
        "snapshot": None,
        "top_holdings": [],
        "concentration": None,
        "style": {"category": style, "category_evidence": style_hit,
                  "passive_verdict": verdict, "passive_evidences": evidences},
        "sector_exposure": [],
        "theme_exposure": [],
        "risk_notes": risks,
        "insights": list(insights or []),
    }
    if snap is not None:
        payload["snapshot"] = {
            "date": snap.date, "count": len(snap.df),
            "n_cash_substitute": snap.n_cash_sub,
            "weight_source": snap.weight_source,
            "weight_estimated": snap.weight_estimated,
        }
        for _, r in snap.df.head(top).iterrows():
            payload["top_holdings"].append({
                "code": r["code"], "name": r["name"],
                "sector": r.get("sector") or None,
                "weight_pct": None if pd.isna(r["weight"]) else round(float(r["weight"]), 4),
                "cash_substitute": r["sub_flag"] or None,
            })
        if conc is not None:
            payload["concentration"] = {
                "top1": conc.top1, "top3": conc.top3, "top5": conc.cr5,
                "top10": conc.cr10, "hhi": conc.hhi,
                "effective_n": conc.eff_n, "level": conc.level,
            }
        if sec_exp is not None and not sec_exp.empty:
            payload["sector_exposure"] = [
                {"sector": r["行业"], "count": int(r["成分数"]),
                 "weight_pct": None if pd.isna(r["权重(%)"]) else float(r["权重(%)"])}
                for _, r in sec_exp.iterrows()]
        if theme_exp is not None and not theme_exp.empty:
            payload["theme_exposure"] = [
                {"theme": r["主题"], "count": int(r["成分数"]),
                 "weight_pct": None if pd.isna(r["权重(%)"]) else float(r["权重(%)"])}
                for _, r in theme_exp.iterrows()]
    return json_safe(payload)


# ── 主流程 ────────────────────────────────────────────────


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="基金隐含风格透视：输入 ETF/基金代码，输出基础信息 + Top 持仓 + 集中度 + 风格分类 + 风险提示")
    p.add_argument("--symbol", required=True,
                   help="基金代码，如 510300.SH、159919.SZ、000001.OF")
    p.add_argument("--date", default=None,
                   help="成分日期 YYYYMMDD（默认最近交易日；自动向前回溯取最新清单）")
    p.add_argument("--lookback", type=int, default=15,
                   help="向前回溯天数窗口（默认 15 天，覆盖周末/节假日）")
    p.add_argument("--top", type=int, default=10, help="Top 持仓展示只数（默认 10）")
    p.add_argument("--no-enrich", action="store_true",
                   help="严格两接口模式：不调用行情/行业补充接口（权重降级为只数统计）")
    p.add_argument("--no-html", action="store_true", help="只输出终端，不生成 HTML")
    p.add_argument("--json", action="store_true",
                   help="输出归一化 JSON（替代 HTML）；--save 指定路径，配合 --no-html 时打印到 stdout")
    p.add_argument("--save", default=None, help="自定义报告保存路径（HTML 或 JSON）")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    symbol = args.symbol.strip().upper()

    console.print(f"[bold]🔍 基金隐含风格透视[/bold]  目标：[cyan]{symbol}[/cyan]")
    pandata_login()

    # 1. 基础信息（任务书接口 1）
    with console.status("查询基金基础信息 get_fund_detail ..."):
        detail = fetch_fund_detail(symbol)
    fund_row = print_fund_card(detail, symbol)

    # 2. 成分快照（任务书接口 2）
    end = args.date or latest_trade_date()
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=args.lookback)).strftime("%Y%m%d")
    with console.status(f"查询 ETF 申赎清单成分 get_fund_etf_constituents [{start}~{end}] ..."):
        raw = fetch_etf_constituents(symbol, start, end)
    snap = build_snapshot(raw)

    # 3. 行情/行业补充（补全成分名称 + 估算权重 + 行业分类）
    if snap and not args.no_enrich:
        with console.status(f"补充成分行情与行业（get_stock_daily / get_stock_detail，{len(snap.df)} 只）..."):
            snap = enrich_snapshot(snap)

    # 4. 分析
    name = str(fund_row.get("name", ""))
    benchmark = str(fund_row.get("benchmark", ""))
    idx_type = str(fund_row.get("index_fund_type", ""))
    idx_name_raw = fund_row.get("index_name", "")
    idx_name = "" if idx_name_raw is None or pd.isna(idx_name_raw) else str(idx_name_raw)
    is_qdii = str(fund_row.get("is_qdii_fund", "0")) in ("1", "1.0")
    style, style_hit = classify_fund_style(name, benchmark, idx_name, is_qdii)
    verdict, evidences = classify_passive(name, benchmark, idx_type, idx_name)
    conc = compute_concentration(snap) if snap else None
    sec_exp = sector_exposure(snap) if snap else pd.DataFrame()
    theme_exp = theme_exposure(snap) if snap else pd.DataFrame()
    risks = build_risks(symbol, style, verdict, snap, conc, is_qdii)
    insights = generate_insights(symbol, fund_row, snap, conc, style, verdict,
                                 sec_exp, theme_exp)

    # 5. 终端输出
    if snap:
        print_snapshot_info(snap)
        print_top_holdings(snap, args.top)
        print_concentration(conc)
    else:
        console.print(Panel("未取到申赎清单成分：该基金可能不是场内 ETF，或当日未公布清单。",
                            title="📦 成分快照", border_style="yellow"))
    print_style(style, style_hit, verdict, evidences)
    if snap:
        print_sector_exposure(sec_exp)
        print_theme_exposure(theme_exp)
    print_insights(insights)
    print_risks(risks)

    # 6. 报告输出：JSON（--json）或 HTML（默认）
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.json:
        payload = build_payload(symbol, fund_row, snap, conc, style, style_hit,
                                verdict, evidences, sec_exp, theme_exp, risks, args.top,
                                insights=insights)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.no_html and not args.save:
            print(text)
        else:
            jout = Path(args.save) if args.save else Path.cwd() / f"fund_xray_{symbol.replace('.', '_')}_{ts}.json"
            jout.write_text(text, encoding="utf-8")
            console.print(f"\n[green]✅ JSON 报告已生成：{jout}[/green]")
    elif not args.no_html:
        out = Path(args.save) if args.save else Path.cwd() / f"fund_xray_{symbol.replace('.', '_')}_{ts}.html"
        render_html(out, symbol=symbol, fund_row=fund_row, snap=snap, conc=conc,
                    style=style, style_hit=style_hit, verdict=verdict, evidences=evidences,
                    sec_exp=sec_exp, theme_exp=theme_exp, risks=risks, top=args.top,
                    insights=insights)
        console.print(f"\n[green]✅ HTML 报告已生成：{out}[/green]")


if __name__ == "__main__":
    main()
