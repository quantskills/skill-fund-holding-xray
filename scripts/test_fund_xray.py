#!/usr/bin/env python3
"""
test_fund_xray.py — skill-fund-holding-xray 测试套件

两部分：
  A. 离线单元测试（合成数据，无需账号）：schema 检测 / 快照 / 行情行业补充 /
     集中度 / 风格分类 / 双判定 / 行业主题暴露 / 风险提示 / HTML 渲染 / main() 端到端
  B. 真实 API 集成测试：检测到 PandaAI 账号（.env 或环境变量）才运行，否则跳过

用法：
  python test_fund_xray.py            # 离线 + （有账号时）在线
  export PANDA_DATA_USERNAME="..."    # 提供账号后自动跑真实接口
  export PANDA_DATA_PASSWORD="..."
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fund_xray as fx  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(cond: bool, label: str):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}")


def skip(label: str, why: str):
    global SKIP
    SKIP += 1
    print(f"  ⏭️  {label}（跳过：{why}）")


# ── 合成数据工厂 ─────────────────────────────────────────────


def fake_raw_constituents() -> pd.DataFrame:
    """经典 schema：自带 stock_name/weight/sub_flag 文本标志。"""
    rows = [
        ("510300.SH", "20260706", "600519.SH", "贵州茅台", 3.0, 100, ""),
        ("510300.SH", "20260706", "300750.SZ", "宁德时代", 2.0, 200, ""),
        ("510300.SH", "20260710", "600519.SH", "贵州茅台", 12.0, 100, ""),
        ("510300.SH", "20260710", "300750.SZ", "宁德时代", 10.0, 200, ""),
        ("510300.SH", "20260710", "600036.SH", "招商银行", 8.0, 300, "必须现金替代"),
        ("510300.SH", "20260710", "688981.SH", "中芯国际", 7.0, 400, ""),
        ("510300.SH", "20260710", "000858.SZ", "五粮液", 6.0, 500, ""),
        ("510300.SH", "20260710", "601318.SH", "中国平安", 5.5, 600, ""),
        ("510300.SH", "20260710", "600900.SH", "长江电力", 5.0, 700, ""),
        ("510300.SH", "20260710", "002594.SZ", "比亚迪", 4.5, 800, ""),
        ("510300.SH", "20260710", "600030.SH", "中信证券", 4.0, 900, ""),
        ("510300.SH", "20260710", "603259.SH", "药明康德", 3.5, 1000, ""),
        ("510300.SH", "20260710", "688111.SH", "金山办公", 3.0, 1100, ""),
        ("510300.SH", "20260710", "601899.SH", "紫金矿业", 2.5, 1200, ""),
    ]
    return pd.DataFrame(rows, columns=[
        "symbol", "date", "stock_symbol", "stock_name", "weight", "amount", "sub_flag"])


def fake_raw_real_schema() -> pd.DataFrame:
    """真实 schema（2026-07-18 实测）：name 列是基金名，无权重/价格，数值替代标志。"""
    FUND = "华泰柏瑞沪深300ETF"
    rows = [
        ("510300.SH", "20260717", FUND, "SH", "600346.SH", 300.0, 1, 34.0, 0.0, None, None),
        ("510300.SH", "20260717", FUND, "SH", "601398.SH", 5200.0, 1, 34.0, 0.0, None, None),
        ("510300.SH", "20260717", FUND, "SH", "003816.SZ", 1300.0, 2, 10.0, 10.0, 5044.0, None),
        ("510300.SH", "20260717", FUND, "SH", "300014.SZ", 200.0, 1, 10.0, 10.0, None, None),
        ("510300.SH", "20260717", FUND, "SH", "000157.SZ", 800.0, 1, 10.0, 10.0, None, None),
        ("510300.SH", "20260716", FUND, "SH", "600346.SH", 290.0, 1, 34.0, 0.0, None, None),
    ]
    return pd.DataFrame(rows, columns=[
        "symbol", "date", "name", "exchange", "stock_symbol", "quantity",
        "cash_substitution_flag", "cash_premium_rate", "cash_discount_rate",
        "fixed_substitution_amount", "redemption_cash_amount"])


def fake_quotes(symbols, date):  # noqa: ARG001
    rows = [
        ("600346.SH", "20260717", 15.06, 15.07, "恒力石化"),
        ("601398.SH", "20260717", 7.57, 7.39, "工商银行"),
        ("003816.SZ", "20260717", 3.94, 3.88, "中国广核"),
        ("300014.SZ", "20260717", 40.0, 39.5, "亿纬锂能"),
        ("000157.SZ", "20260717", 8.0, 7.9, "中联重科"),
    ]
    return pd.DataFrame(rows, columns=["symbol", "date", "close", "pre_close", "name"])


def fake_sectors(symbols):  # noqa: ARG001
    rows = [
        ("600346.SH", "原材料"), ("601398.SH", "金融"), ("003816.SZ", "公用事业"),
        ("300014.SZ", "工业"), ("000157.SZ", "工业"),
    ]
    return pd.DataFrame(rows, columns=["symbol", "sector_code_name"])


def fake_detail() -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": "510300.SH", "name": "华泰柏瑞沪深300ETF", "type": "E",
        "management_institution": "华泰柏瑞基金管理有限公司",
        "custodian_institution": "中国工商银行股份有限公司",
        "benchmark": "沪深300指数", "index_fund_type": "I", "index_name": "沪深300指数",
        "etf_lof_type": "ETF", "status": "L", "fund_status": "A", "is_qdii_fund": 0,
        "found_date": "20120504", "listed_date": "20120528",
    }])


def patch_enrich():
    fx.fetch_stock_quotes = fake_quotes
    fx.fetch_stock_sectors = fake_sectors


# ── A. 离线单元测试 ──────────────────────────────────────────


def test_detect_columns():
    print("\n[A1] detect_columns —— schema 自适应")
    m = fx.detect_columns(fake_raw_constituents())
    check(m["code"] == "stock_symbol", "识别成分券代码列")
    check(m["name"] == "stock_name", "识别成分券名称列")
    check(m["weight"] == "weight", "识别权重列")
    check(m["shares"] == "amount", "识别数量列")
    check(m["sub_flag"] == "sub_flag", "识别现金替代标志列")
    check(m["date"] == "date", "识别日期列")
    m2 = fx.detect_columns(fake_raw_real_schema())
    check(m2["shares"] == "quantity", "真实 schema：识别 quantity 为数量列")
    check(m2["sub_flag"] == "cash_substitution_flag", "真实 schema：识别替代标志列")
    check(m2["sub_amount"] == "fixed_substitution_amount", "真实 schema：识别固定替代金额列")


def test_build_snapshot():
    print("\n[A2] build_snapshot —— 单日快照 + 权重归一化")
    snap = fx.build_snapshot(fake_raw_constituents())
    check(snap is not None, "快照生成成功")
    check(snap.date == "20260710", "只保留最新交易日（跨调仓日不可比）")
    check(len(snap.df) == 12, "快照成分只数 = 12（旧日期的 2 条被丢弃）")
    check(abs(snap.df["weight"].sum() - 100.0) < 0.01, "权重按成分口径归一化到 100%")
    check(snap.df.iloc[0]["name"] == "贵州茅台", "按权重降序，第一为贵州茅台")
    check(snap.n_cash_sub == 1, "现金替代计数 = 1")
    check("归一化" in snap.weight_source, "权重来源注明归一化口径")
    empty = fx.build_snapshot(pd.DataFrame())
    check(empty is None, "空数据返回 None（非 ETF 降级路径）")


def test_real_schema_snapshot():
    print("\n[A3] build_snapshot —— 真实 schema（基金名列/数值标志/无权重）")
    snap = fx.build_snapshot(fake_raw_real_schema())
    check(snap.date == "20260717", "取最新交易日 20260717")
    check(len(snap.df) == 5, "5 只成分（旧日期 1 条被丢弃）")
    check((snap.df["name"] == snap.df["code"]).all(), "基金名列被识别并置回代码（不用错列）")
    check(snap.df["weight"].isna().all(), "无权重/价格 → 权重暂缺待补充")
    check("待行情接口补充" in snap.weight_source, "口径注明待行情补充")
    check(snap.n_cash_sub == 1, "必须现金替代计数 = 1（flag=2 + 固定替代金额）")
    check((snap.df["sub_flag"] == "必须").sum() == 1, "数值标志 2 映射为「必须」")


def test_enrich():
    print("\n[A4] enrich_snapshot —— 行情/行业补充 + 权重估算")
    patch_enrich()
    snap = fx.build_snapshot(fake_raw_real_schema())
    snap = fx.enrich_snapshot(snap)
    check("恒力石化" in set(snap.df["name"]), "成分名称由行情接口补全")
    check(snap.df["weight"].notna().all(), "权重 = quantity × 收盘价 已估算")
    check(abs(snap.df["weight"].sum() - 100.0) < 0.01, "估算权重归一化到 100%")
    top1 = snap.df.iloc[0]
    check(top1["name"] == "工商银行" and top1["weight"] > 60,
          f"第一大权重 = 工商银行（{top1['weight']}%）")
    check(snap.weight_estimated is True, "标记权重为估算口径")
    check("get_stock_daily" in snap.weight_source, "口径注明补充接口")
    check(set(snap.df["sector"]) >= {"金融", "工业", "公用事业"}, "行业分类已合并")
    sec = fx.sector_exposure(snap)
    check(not sec.empty and sec.iloc[0]["行业"] == "金融",
          "行业暴露第一 = 金融（工商银行 62%）")


def test_concentration():
    print("\n[A5] compute_concentration —— CR5/CR10/HHI")
    snap = fx.build_snapshot(fake_raw_constituents())
    conc = fx.compute_concentration(snap)
    check(conc.n == 12, "成分只数 n=12")
    check(abs(conc.top1 - 16.9) < 0.01, f"Top1 = {conc.top1}%（12/71 归一）")
    check(abs(conc.top3 - 42.25) < 0.01, f"Top3 = {conc.top3}%（30/71 归一）")
    check(conc.cr5 is not None and 55 < conc.cr5 < 65, f"CR5 合理区间（实际 {conc.cr5}%）")
    check(conc.cr10 is not None and 85 < conc.cr10 < 95, f"CR10 合理区间（实际 {conc.cr10}%）")
    check(conc.hhi is not None and 0 < conc.hhi < 1, "HHI ∈ (0,1)")
    check(conc.eff_n is not None and conc.eff_n > 1, "有效成分数 > 1")
    check("集中" in conc.level, "输出定性解读")


def test_classification():
    print("\n[A6] 风格粗分类 + 被动/Smart Beta 双判定")
    s, _ = fx.classify_fund_style("华泰柏瑞沪深300ETF", "沪深300指数", "沪深300指数")
    check(s == "宽基", "沪深300 → 宽基")
    s, _ = fx.classify_fund_style("中证红利低波动ETF", "中证红利低波动指数")
    check(s == "Smart Beta/因子", "红利低波 → Smart Beta/因子")
    s, _ = fx.classify_fund_style("纳指ETF", "纳斯达克100指数")
    check(s == "跨境/QDII", "纳斯达克 → 跨境/QDII")
    s, hit = fx.classify_fund_style("某基金", "无", is_qdii=True)
    check(s == "跨境/QDII" and "is_qdii" in hit, "is_qdii_fund=1 → 跨境/QDII（数据源标记）")
    s, _ = fx.classify_fund_style("黄金ETF", "上海金价格")
    check(s == "商品", "黄金 → 商品")
    s, _ = fx.classify_fund_style("某某半导体设备ETF", "中证半导体产业指数")
    check(s.startswith("行业/主题"), "半导体设备 → 行业/主题（兜底）")

    v, ev = fx.classify_passive("华泰柏瑞沪深300ETF", "沪深300指数", "I", "沪深300指数")
    check(v == "被动指数跟踪", "I + 指数基准 → 被动指数跟踪")
    v, _ = fx.classify_passive("某指数增强ETF", "中证500指数", "EI")
    check("指数增强" in v, "EI → 指数增强")
    v, _ = fx.classify_passive("红利低波ETF", "中证红利低波指数", "I")
    check("Smart Beta" in v, "红利低波关键词 → Smart Beta 判定")
    v, _ = fx.classify_passive("某主动基金", "无", "UN")
    check("主动" in v, "UN + 无指数基准 → 疑似主动")
    v, _ = fx.classify_passive("易方达优质精选混合", "沪深300指数收益率*50%+中证香港300指数", "UN")
    check("主动" in v and "被动" not in v, "UN + 基准含指数 → 主动管理（不误判为被动）")
    check(any("index_fund_type" in e for e in ev), "判定输出证据链")


def test_theme_exposure():
    print("\n[A7] theme_exposure —— 成分主题权重暴露")
    snap = fx.build_snapshot(fake_raw_constituents())
    exp = fx.theme_exposure(snap)
    check(not exp.empty, "主题暴露非空")
    themes = set(exp["主题"])
    check("消费/白酒" in themes, "命中 消费/白酒（茅台/五粮液）")
    check("半导体/芯片" in themes, "命中 半导体/芯片（中芯国际）")
    check("新能源" in themes, "命中 新能源（宁德/比亚迪）")
    check(exp.attrs.get("has_weight") is True, "带权重口径")
    check(0 < exp.attrs.get("matched_weight", 0) <= 100, "已匹配权重合计 ∈ (0,100]")


def test_risks():
    print("\n[A8] build_risks —— 风险提示生成")
    snap = fx.build_snapshot(fake_raw_constituents())
    conc = fx.compute_concentration(snap)
    risks = fx.build_risks("510300.SH", "宽基", "被动指数跟踪", snap, conc)
    text = "\n".join(risks)
    check("须用权重 × 基金" in text, "含权重≠持仓口径提醒")
    check("跨调仓日" in text, "含跨调仓日不可比提醒")
    check("主观" in text, "含主题分类主观性提醒")
    check("集中度风险" in text, "CR10 高 → 触发集中度风险")
    check("现金替代" in text, "含现金替代提醒")
    patch_enrich()
    snap2 = fx.enrich_snapshot(fx.build_snapshot(fake_raw_real_schema()))
    r2 = fx.build_risks("510300.SH", "宽基", "被动指数跟踪", snap2,
                        fx.compute_concentration(snap2))
    check(any("估算" in r for r in r2), "估算权重 → 触发口径差异提醒")
    r3 = fx.build_risks("110011.OF", "行业/主题", "疑似主动", None, None)
    check("可能不是场内 ETF" in r3[0], "非 ETF → 首条提示无成分数据")
    r4 = fx.build_risks("513100.SH", "跨境/QDII", "被动指数跟踪", snap, conc, is_qdii=True)
    check(any("汇率" in r for r in r4), "跨境 → 触发汇率/时差风险")


def test_html_render():
    print("\n[A9] render_html —— HTML 报告渲染")
    patch_enrich()
    snap = fx.enrich_snapshot(fx.build_snapshot(fake_raw_real_schema()))
    conc = fx.compute_concentration(snap)
    sec = fx.sector_exposure(snap)
    theme = fx.theme_exposure(snap)
    risks = fx.build_risks("510300.SH", "宽基", "被动指数跟踪", snap, conc)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "report.html"
        fx.render_html(out, symbol="510300.SH", fund_row=fake_detail().iloc[0].to_dict(),
                       snap=snap, conc=conc, style="宽基", style_hit="命中「沪深300」",
                       verdict="被动指数跟踪", evidences=["index_fund_type=I"],
                       sec_exp=sec, theme_exp=theme, risks=risks, top=10,
                       insights=["测试洞察：工商银行居首"])
        html = out.read_text(encoding="utf-8")
    check("华泰柏瑞沪深300ETF" in html, "HTML 含基金名称")
    check("工商银行" in html, "HTML 含 Top 成分")
    check("AI 数据洞察" in html, "HTML 含 AI 数据洞察卡片")
    check("测试洞察：工商银行居首" in html, "HTML 洞察内容已注入")
    check("<th>行业</th>" in html, "HTML Top 持仓表含行业列")
    check("Top1 权重(%)" in html, "HTML 指标卡含 Top1")
    check("金融" in html, "HTML 含行业暴露")
    check("风险提示" in html, "HTML 含风险提示")
    check("echarts" in html.lower(), "HTML 含 ECharts")
    check("#533afd" in html, "HTML 用模板主色 #533afd")
    # 回归：模板中的 \n 必须转义为 \\n，否则生成 JS 字符串含字面换行 → SyntaxError → 图表全不渲染
    check(r"formatter:'{b}\n{c}%'" in html, "环形图标签换行为转义后的 \\n（防 JS SyntaxError）")
    check("formatter:'{b}\n{c}%'" not in html, "HTML 无字面换行的 JS 字符串")


def test_json_payload():
    print("\n[A10] build_payload —— 归一化 JSON 输出")
    import json as jsonlib
    check(fx.json_safe(float("nan")) is None, "json_safe: NaN → None")
    check(fx.json_safe({"a": [pd.NA, 1.5]}) == {"a": [None, 1.5]}, "json_safe: 递归处理")
    patch_enrich()
    snap = fx.enrich_snapshot(fx.build_snapshot(fake_raw_real_schema()))
    conc = fx.compute_concentration(snap)
    sec = fx.sector_exposure(snap)
    theme = fx.theme_exposure(snap)
    risks = fx.build_risks("510300.SH", "宽基", "被动指数跟踪", snap, conc)
    payload = fx.build_payload("510300.SH", fake_detail().iloc[0].to_dict(), snap, conc,
                               "宽基", "命中「沪深300」", "被动指数跟踪",
                               ["index_fund_type=I"], sec, theme, risks, 10,
                               insights=["洞察A"])
    text = jsonlib.dumps(payload, ensure_ascii=False)
    check("工商银行" in text, "JSON 可序列化且含 Top 成分")
    check(payload["insights"] == ["洞察A"], "payload 含 insights 字段")
    check(payload["snapshot"]["count"] == 5, "snapshot.count = 5")
    check(payload["snapshot"]["weight_estimated"] is True, "snapshot 标记估算口径")
    check(payload["top_holdings"][0]["sector"] == "金融", "Top 持仓含行业字段")
    check(payload["top_holdings"][0]["weight_pct"] > 60, "Top1 权重 > 60%")
    check(payload["concentration"]["top1"] is not None
          and payload["concentration"]["effective_n"] is not None,
          "concentration 含 top1/effective_n")
    check(payload["style"]["passive_verdict"] == "被动指数跟踪", "style 含双判定结论")
    check(payload["sector_exposure"][0]["sector"] == "金融", "行业暴露第一 = 金融")
    check(isinstance(payload["risk_notes"], list) and payload["risk_notes"], "风险清单非空")
    # 非 ETF 降级负载
    p2 = fx.build_payload("110011.OF", {}, None, None, "行业/主题", "-",
                          "主动管理", [], pd.DataFrame(), pd.DataFrame(), ["提示"], 10)
    check(p2["snapshot"] is None and p2["concentration"] is None,
          "非 ETF：snapshot/concentration 为 null")
    check(jsonlib.dumps(p2, ensure_ascii=False) is not None, "降级负载可序列化")


def test_offline_e2e():
    print("\n[A11] main() 离线端到端（mock 数据层 + 登录 + 补充接口）")
    fx.pandata_login = lambda: None
    fx.fetch_fund_detail = lambda symbol: fake_detail()
    fx.fetch_etf_constituents = lambda symbol, s, e: fake_raw_real_schema()
    fx.latest_trade_date = lambda: "20260717"
    patch_enrich()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "e2e.html"
        fx.main(["--symbol", "510300.SH", "--date", "20260717", "--save", str(out)])
        html = out.read_text(encoding="utf-8") if out.exists() else ""
        check(out.exists() and out.stat().st_size > 5000, "端到端跑通并生成 HTML")
        check("工商银行" in html and "估算" in html, "HTML 含估算权重成分与口径披露")
        check("AI 数据洞察" in html and "工商银行" in html.split("AI 数据洞察")[-1],
              "端到端 HTML 含 AI 数据洞察（内容紧跟卡片）")
    # --json 输出模式
    with tempfile.TemporaryDirectory() as td:
        jout = Path(td) / "e2e.json"
        fx.main(["--symbol", "510300.SH", "--date", "20260717", "--json", "--save", str(jout)])
        import json as jsonlib
        data = jsonlib.loads(jout.read_text(encoding="utf-8"))
        check(data["symbol"] == "510300.SH" and data["top_holdings"][0]["name"] == "工商银行",
              "--json 模式端到端生成归一化 JSON")
        check(isinstance(data.get("insights"), list) and len(data["insights"]) >= 4,
              "--json 模式含 AI 数据洞察（≥4 条）")
    # 非 ETF 降级路径
    fx.fetch_etf_constituents = lambda symbol, s, e: pd.DataFrame()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "e2e_nonetf.html"
        fx.main(["--symbol", "110011.OF", "--date", "20260717", "--save", str(out)])
        html = out.read_text(encoding="utf-8")
        check("可能不是场内 ETF" in html, "非 ETF 降级路径正常")


def test_insights():
    print("\n[A12] generate_insights —— 数据驱动 AI 洞察")
    patch_enrich()
    snap = fx.enrich_snapshot(fx.build_snapshot(fake_raw_real_schema()))
    conc = fx.compute_concentration(snap)
    sec = fx.sector_exposure(snap)
    theme = fx.theme_exposure(snap)
    ins = fx.generate_insights("510300.SH", fake_detail().iloc[0].to_dict(),
                               snap, conc, "宽基", "被动指数跟踪", sec, theme)
    text = "\n".join(ins)
    check(len(ins) >= 4, f"洞察条数 ≥ 4（实际 {len(ins)}）")
    check("工商银行" in text and "权重" in text, "含第一大权重股洞察")
    check("前十大" in text and "HHI" in text, "含集中度洞察（CR10 + HHI）")
    check("行业暴露以「金融」为首" in text, "含行业暴露洞察")
    check("主题暴露以" in text, "含主题暴露洞察")
    check("综合判定：宽基 · 被动指数跟踪" in text, "含综合判定")
    check("必须现金替代" in text, "n_cash_sub>0 → 触发替代洞察")
    check("单票波动对基金净值影响显著" in text, "Top1 > 15% → 触发单票敏感度提示")
    # 非 ETF 降级路径（snap=None 不得崩溃）
    ins2 = fx.generate_insights("110011.OF", {}, None, None, "行业/主题", "主动管理",
                                pd.DataFrame(), pd.DataFrame())
    check(len(ins2) == 2 and "不可用" in ins2[0], "非 ETF 降级洞察正常（2 条兜底）")
    check(any("主动管理" in i for i in ins2), "降级路径仍输出综合判定")


# ── B. 真实 API 集成测试 ──────────────────────────────────────


def test_live_api():
    print("\n[B] 真实 PandaAI API 集成测试")
    import importlib
    importlib.reload(fx)  # 撤销离线测试的 monkeypatch，恢复真实数据层
    fx._load_env()
    user = os.environ.get("PANDA_DATA_USERNAME")
    pwd = os.environ.get("PANDA_DATA_PASSWORD")
    if not user or not pwd:
        skip("get_fund_detail 真实调用", "未配置 PANDA_DATA_USERNAME/PASSWORD")
        skip("get_fund_etf_constituents 真实调用", "未配置账号")
        return
    try:
        import panda_data
        panda_data.init_token(username=user, password=pwd,
                              base_url=os.environ.get("PANDA_DATA_BASE_URL", fx.PANDA_BASE_URL))
    except Exception as exc:  # noqa: BLE001
        skip("登录", f"init_token 失败：{exc}")
        return

    d = fx.fetch_fund_detail("510300.SH")
    check(not d.empty, "get_fund_detail(510300.SH) 返回非空")
    if not d.empty:
        check({"name", "benchmark", "index_fund_type"}.issubset(d.columns),
              "detail 含 name/benchmark/index_fund_type")

    end = fx.latest_trade_date()
    start = (pd.Timestamp(end) - pd.Timedelta(days=15)).strftime("%Y%m%d")
    raw = fx.fetch_etf_constituents("510300.SH", start, end)
    check(raw is not None and not raw.empty, "get_fund_etf_constituents 返回非空")
    if raw is not None and not raw.empty:
        check({"symbol", "date", "stock_symbol", "quantity"}.issubset(raw.columns),
              "成分券含 symbol/date/stock_symbol/quantity")
        snap = fx.build_snapshot(raw)
        check(snap is not None and len(snap.df) > 100,
              f"真实快照成分 > 100 只（实际 {len(snap.df) if snap else 0}）")
        snap = fx.enrich_snapshot(snap)
        n_named = int((snap.df["name"] != snap.df["code"]).sum())
        check(n_named > 100, f"成分名称补全 > 100 只（实际 {n_named}）")
        check(snap.df["weight"].notna().any(), "估算权重已生成")
        conc = fx.compute_concentration(snap)
        check(conc.cr10 is not None and 0 < conc.cr10 <= 100,
              f"真实 CR10 计算成功（{conc.cr10}%）")


if __name__ == "__main__":
    print("=" * 64)
    print("skill-fund-holding-xray 测试套件")
    print("=" * 64)
    test_detect_columns()
    test_build_snapshot()
    test_real_schema_snapshot()
    test_enrich()
    test_concentration()
    test_classification()
    test_theme_exposure()
    test_risks()
    test_html_render()
    test_json_payload()
    test_offline_e2e()
    test_insights()
    test_live_api()
    print("\n" + "=" * 64)
    print(f"结果：✅ 通过 {PASS}   ❌ 失败 {FAIL}   ⏭️  跳过 {SKIP}")
    print("=" * 64)
    sys.exit(1 if FAIL else 0)
