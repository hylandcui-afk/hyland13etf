# -*- coding: utf-8 -*-
"""一键生成网站数据 docs/data/results.json。

跑什么：
  1. 拉原池(S3) 13只ETF + 沪深300指数 + 上证指数 的腾讯前复权日线
  2. engine.backtest 2021起月度调仓回测（口径同 backtest.py --pool S3）
  3. 计算指标 / 分年段 / 每月持仓 / 本月首日持仓 / 最新信号

失败兜底：数据拉不全（任一标的0条或<500条）则不覆盖 results.json，exit 0。
用法: python build_results.py
"""
import json
import math
import os
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tencent_data as td
from pools import POOLS
import engine

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS_DATA = os.path.join(ROOT, "docs", "data")
OUT_JSON = os.path.join(DOCS_DATA, "results.json")
CACHE = os.path.join(HERE, "cache.csv")

S3 = POOLS["S3"]
BENCHES = {"sh.000300": "hs300", "sh.000001": "shanghai"}   # 指数代码 → json键
START = "2021-01-01"


def load_prices(today):
    """拉全部标的+指数，返回 (px, bench)。失败则回退本地缓存，缓存也没有才返回 None。"""
    df = td.tencent_qfq_all(S3["codes"], end=today)
    bad = [c for c in S3["codes"] if c not in df.columns or df[c].dropna().shape[0] < 500]
    if bad:
        if os.path.exists(CACHE):
            print(f"⚠ 在线拉取不全（{len(bad)}只缺数据），回退本地缓存 {os.path.basename(CACHE)}")
            df = pd.read_csv(CACHE, index_col=0, parse_dates=True).sort_index()
        else:
            print(f"❌ 数据不全且无缓存: {bad}，不覆盖 results.json")
            return None, None
    else:
        df.to_csv(CACHE, float_format="%.4f")

    bench = {}
    for code in BENCHES:
        s = pd.Series(dtype=float)
        for attempt in range(5):          # 指数限流时等20秒再试，共约100秒
            s = td.tencent_qfq(code, end=today)
            if not s.empty:
                break
            print(f"  ⏳ 指数 {code} 第{attempt + 1}次拉取失败，20秒后重试…")
            time.sleep(20)
        bcache = os.path.join(HERE, f"bench_{BENCHES[code]}.csv")
        if s.empty:
            if os.path.exists(bcache):
                print(f"⚠ 指数 {code} 在线拉取失败，回退缓存")
                s = pd.read_csv(bcache, index_col=0, parse_dates=True).iloc[:, 0].sort_index()
            elif code == "sh.000300" and "sh.510300" in df.columns:
                # 兜底：沪深300ETF代理（qfq归一化走势≈指数）
                print("⚠ 沪深300指数不可得，用 sh.510300 沪深300ETF 代理")
                s = df["sh.510300"].dropna()
                s.name = "sh.000300"
            else:
                print(f"❌ 指数拉取失败且无缓存: {code}")
                return None, None
        else:
            s.to_frame("close").to_csv(bcache, float_format="%.4f")
        bench[BENCHES[code]] = s
    return df, bench


def normalize(series, index):
    """对齐 index 并归一到起点=1。"""
    a = series.reindex(index, method="ffill")
    return a / a.iloc[0]


def metrics_from(eq, bench_series=None, riskfree=0.02):
    """基础回测指标。bench_series 传入时同时算基准收益与超额。"""
    ret = eq.iloc[-1] / eq.iloc[0] - 1
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    ann = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0.1 else ret
    mdd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    vol = r.std() * math.sqrt(250)
    shp = (r.mean() * 250 - riskfree) / vol if vol > 0 else 0.0
    calmar = ann / abs(mdd) if mdd < 0 else None
    # 月度胜率：按自然月首末净值
    m = eq.resample("ME").last().pct_change().dropna()
    win = float((m > 0).mean()) if len(m) else None
    out = {
        "total_return": ret, "annual_return": ann, "max_drawdown": mdd,
        "sharpe": shp, "calmar": calmar, "annual_vol": vol, "monthly_win_rate": win,
        "years": yrs,
    }
    if bench_series is not None:
        b = bench_series.reindex(eq.index, method="ffill").dropna()
        b = b[b.index >= eq.index[0]]
        bret = b.iloc[-1] / b.iloc[0] - 1
        out["bench_return"] = bret
        out["excess_return"] = ret - bret
    return out


def build():
    today = datetime.now().strftime("%Y-%m-%d")
    px, bench = load_prices(today)
    if px is None:
        return False

    # ── 回测（口径同 backtest.py --pool S3）──
    record = []
    eq = engine.backtest(px, codes=S3["codes"], wave_max=S3["wave_max"],
                         start=START, record=record)
    eq = eq[eq.index >= pd.Timestamp(START)]   # 引擎返回全历史(起点前平仓段)，展示切到起点起（同backtest.py --report）

    # 每月持仓快照 → 行（不足50元的微调不记录，和回测一致）；日期转字符串
    rows = [{"date": str(r["date"].date()), **{k: v for k, v in r.items() if k != "date"}}
            for r in record if len(r) > 1]

    # ── 指标 ──
    m = metrics_from(eq)
    m["bench_hs300"] = metrics_from(eq, bench["hs300"])["bench_return"]
    m["bench_shanghai"] = metrics_from(eq, bench["shanghai"])["bench_return"]
    m["excess_hs300"] = m["total_return"] - m["bench_hs300"]
    m["excess_shanghai"] = m["total_return"] - m["bench_shanghai"]

    # ── 分年段 ──
    seg_df = engine.segment_table(eq)
    segments = []
    for _, row in seg_df.iterrows():
        segments.append({k: (None if pd.isna(v) else float(v) if isinstance(v, (int, float, np.floating)) else v)
                         for k, v in row.items()})

    # ── 本月首日持仓 + 最新信号（逻辑同 daily_run.run_once）──
    this_month = datetime.now().strftime("%Y-%m")
    month_dates = [d for d in px.index if d.strftime("%Y-%m") == this_month]
    blocks = {}
    if month_dates:
        first_day = month_dates[0]
        prev_first = px.index[px.index < first_day][-1]
        blocks["month_first"] = signal_block(px, first_day, prev_first)
    last_day = px.index[-1]
    blocks["latest"] = signal_block(px, None, last_day)

    results = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "data_end": str(px.index[-1].date()),
            "pool_label": S3["label"],
            "start": START,
            "names": S3["names"],   # 全池 代码→名称，前端列名统一用它
            "params": {"mom": engine.MOM, "top_n": engine.TOP_N, "lookback": engine.LOOKBACK,
                       "w": engine.W, "slippage": engine.SLIP, "commission": engine.COMM},
        },
        "nav": {
            "dates": [str(d.date()) for d in eq.index],
            "strategy": [round(float(v), 6) for v in eq.values / 100000.0],
            "hs300": [round(float(v), 6) for v in normalize(bench["hs300"], eq.index).values],
            "shanghai": [round(float(v), 6) for v in normalize(bench["shanghai"], eq.index).values],
        },
        "metrics": m,
        "segments": segments,
        "monthly_holdings": rows,
        "current": blocks,
    }

    os.makedirs(DOCS_DATA, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, default=float)
    print(f"💾 results.json → {OUT_JSON}  ({len(eq)}天净值, {len(rows)}个月持仓)")
    return True


def signal_block(px, rebalance_day, d_prev):
    """截至 d_prev 的排名+前3持仓。rebalance_day 仅用于展示。"""
    ranked, filtered = engine.rank_universe(px, d_prev, mom=engine.MOM,
                                            wave_max=S3["wave_max"],
                                            names=S3["names"], verbose=False)
    targets = filtered[:engine.TOP_N]
    wts = engine.epo_for_targets(px, targets, d_prev)
    ranked_list = [{"code": c, "name": S3["names"].get(c, c), "score": round(float(s), 4)}
                   for c, s in ranked]
    tgt_list = []
    if wts is not None:
        for c, w_ in zip(targets, wts):
            if w_ > 0.001:
                tgt_list.append({"code": c, "name": S3["names"].get(c, c), "weight": round(float(w_), 4)})
    return {"rebalance_date": str(rebalance_day.date()) if rebalance_day is not None else None,
            "data_asof": str(d_prev.date()),
            "ranked": ranked_list, "targets": tgt_list}


if __name__ == "__main__":
    ok = build()
    sys.exit(0 if ok else 0)   # 失败也不报错退出：Actions 里不覆盖旧JSON即为兜底
