# -*- coding: utf-8 -*-
"""核心引擎：动量打分 / 年化波动 / EPO优化 / 历史回测 / 单期调仓
口径与本地已复现的聚宽策略（openhe post/47208）完全一致：
  动量 Score = 年化收益 × R²（34天对数价格线性回归，年化=e^(slope×250)-1，
             R²=1-SSR/((n-1)·var(ddof=1))，仅 score>0 入选）
  EPO anchored: shrunk_cor=((1-w)·I@corr)+(w·I)，cov_tilde=std@shrunk_cor@std，
             gamma=√(a'·cov_tilde·a)/√(s'·inv·cov_tilde·inv·s)，负权重裁0归一化
  波动过滤: 近LOOKBACK日 log收益 std×√250 > wave_max 则剔除
  回测: 每月首个交易日收盘调仓，Slippage 0.2%，佣金 0.02%
"""
import math

import numpy as np
import pandas as pd

# 默认参数（对应聚宽代码里的值）
MOM = 34
TOP_N = 3
LOOKBACK = 1200
W = 0.2
SLIP = 0.002     # 单边滑点 0.2%
COMM = 0.0002    # 单边佣金 0.02%


# ───────────────────────── 因子 / 优化 ─────────────────────────
def momentum_score(hist, mom=MOM):
    """年化收益×R² 动量分。hist=某标的截至调仓日前一日的收盘序列。"""
    y = hist.values[-mom:]
    if len(y) < mom or np.any(y <= 0):
        return -999.0
    yl = np.log(y)
    x = np.arange(mom, dtype=float)
    slope, b = np.polyfit(x, yl, 1)
    ann = math.exp(slope * 250) - 1
    r2 = 1 - np.sum((yl - (slope * x + b)) ** 2) / ((mom - 1) * np.var(yl, ddof=1)) if np.var(yl, ddof=1) > 0 else 0
    return ann * r2


def vol_ann(hist, lookback=LOOKBACK):
    """年化波动：近lookback日 log收益 std × √250。数据不足返回 NaN。"""
    r = np.diff(np.log(hist.values[-min(lookback, len(hist)):]))
    return r.std() * math.sqrt(250) if len(r) > 30 else np.nan


def atr_frac(hist, lookback=20):
    """近lookback日 |对数收益| 均值（收盘口径的日波动代理，价格分数单位）。
    无 high/low 数据时用它近似 ATR；吊灯止损 = 峰值 − band×atr_frac。"""
    y = hist.values[-(lookback + 1):]
    if len(y) < lookback + 1:
        return np.nan
    return float(np.abs(np.diff(np.log(y))).mean())


def epo_weights(rets, w=W):
    """EPO anchored 权重（逆方差anchor，负权重裁0后归一化）。rets=收益率DataFrame。"""
    n = rets.shape[1]
    if n == 1:
        return np.array([1.0])
    vcov = rets.cov().values
    corr = rets.corr().values
    I = np.eye(n)
    V = np.diag(np.diag(vcov))
    std = np.sqrt(V)
    d = np.diag(vcov)
    anchor = (1 / d) / (1 / d).sum() if np.sum(1 / d) > 0 else np.ones(n) / n
    s = rets.mean().values
    shrunk_cor = ((1 - w) * I @ corr) + (w * I)
    cov_tilde = std @ shrunk_cor @ std
    try:
        inv = np.linalg.solve(cov_tilde, I)
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(cov_tilde)
    gamma = np.sqrt(anchor.T @ cov_tilde @ anchor) / np.sqrt(s.T @ inv @ cov_tilde @ inv @ s)
    ww = inv @ (((1 - w) * gamma * s) + (w * I @ V @ anchor))
    ww = np.array([0 if v < 0 else v for v in ww])
    return ww / ww.sum() if ww.sum() > 1e-12 else np.ones(n) / n


# ───────────────────────── 单期调仓 ─────────────────────────
def rank_universe(df, d_prev, mom=MOM, wave_max=None, lookback=LOOKBACK, names=None, verbose=False):
    """截至 d_prev 给池子各标动量排序；返回 (sorted[(code,score)...], 过滤后score>0列表)。
    wave_max 非 None 时，先剔除年化波动>wave_max 的标的。"""
    scores = {}
    for code in df.columns:
        hist = df.loc[:d_prev, code].dropna()
        if wave_max is not None:
            v = vol_ann(hist, lookback)
            if not np.isnan(v) and v > wave_max:
                if verbose:
                    nm = names.get(code, code) if names else code
                    print(f"    ✂ 波动过滤剔除 {code} {nm:<8} vol={v*100:.1f}% > {wave_max*100:.0f}%")
                continue
        s = momentum_score(hist, mom)
        if s > 0:
            scores[code] = s
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return ranked, [c for c, _ in ranked]


def epo_for_targets(df, targets, d_prev, lookback=LOOKBACK, w=W):
    """给定入选 targets，用近lookback日协方差做EPO，返回权重数组（None=数据不足）。"""
    if not targets:
        return None
    p = df.loc[:d_prev, targets].dropna()
    r = p.pct_change().dropna().iloc[-lookback:]
    if len(r) < 30:
        return None
    return epo_weights(r, w)


# ───────────────────────── 历史回测 ─────────────────────────
def backtest(df, codes=None, mom=MOM, top_n=TOP_N, wave_max=None, lookback=LOOKBACK,
             w=W, slip=SLIP, comm=COMM, start="2021-01-01", verbose=False, record=None,
             stop_floor=None, stop_band=None, rotate=True, events=None):
    """从 start 起，每月首个交易日调仓的净值曲线。
    df=全池价格DataFrame（列含 codes）；返回 pd.Series(日期→净值)。
    record 传入 list 时，每次调仓成交后在当日收盘价下追加快照
    {'date': Timestamp, code: 该标的市值占净值权重, ...}（不改动返回值）。

    事件止损（默认关闭；stop_floor 或 stop_band 任一给定即开启）：
      月度屏幕日 把全部持仓峰值重置为当日收盘；其余交易日收盘逐个持仓检查
      回撤 dd = 1 - 收盘/持仓期峰值；dd > stop_floor（绝对底线，如0.12）
      或 dd > stop_band×ATR20（吊灯线）即清仓该标的。
      卖出后若 rotate：池内 score>0、过波动过滤、且 ≠ 该标的 ≠ 现持仓的候补中，
      轮入排名第一者（以释放资金），否则空仓等月度屏幕；当月不回买止损标的。
    events 传入 list 时记录每次止损 {'date','code','dd%','reason','after'}。"""
    if codes is not None:
        df = df[codes]
    dates = df.index
    # 每月首个交易日
    mf = pd.Series(dates).groupby([dates.year, dates.month]).first().values
    mf = [pd.Timestamp(d) for d in mf if pd.Timestamp(d) >= pd.Timestamp(start)]

    cash = 100000.0
    holdings = {}
    eq = {}
    stop_active = (stop_floor is not None and stop_floor > 0) or (stop_band is not None)
    peak = {}
    for d in dates:
        if d in mf:
            prev_dates = dates[dates < d]
            if len(prev_dates) == 0:
                continue
            prev = prev_dates[-1]
            scores = {}
            for c in df.columns:
                hist = df.loc[:prev, c].dropna()
                if wave_max is not None:
                    v = vol_ann(hist, lookback)
                    if not np.isnan(v) and v > wave_max:
                        continue
                s = momentum_score(hist, mom)
                if s > 0:
                    scores[c] = s
            targets = [c for c, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:top_n]]
            wts = {}
            if targets:
                w_arr = epo_for_targets(df, targets, prev, lookback, w)
                if w_arr is not None:
                    wts = dict(zip(targets, w_arr))
            # 卖出不在目标里的
            for c in list(holdings.keys()):
                if c not in targets:
                    pv = df.loc[d, c]
                    if not np.isnan(pv):
                        cash += holdings[c] * pv * (1 - slip) * (1 - comm)
                        del holdings[c]
            # 买入目标
            if targets and wts:
                hv = sum(holdings.get(c, 0) * (df.loc[d, c] if not np.isnan(df.loc[d, c]) else 0) for c in holdings)
                tot = cash + hv
                for c, tw in wts.items():
                    pv = df.loc[d, c]
                    if np.isnan(pv):
                        continue
                    tgt = tot * tw * (1 - slip)
                    cur = holdings.get(c, 0) * pv
                    diff = tgt - cur
                    if abs(diff) < 50:
                        continue
                    if diff > 0:
                        cost = diff * (1 + comm)
                        if cost <= cash:
                            holdings[c] = holdings.get(c, 0) + diff / pv
                            cash -= cost
                    else:
                        rem = min(-diff * (1 + comm), holdings.get(c, 0) * pv)
                        cash += rem
                        holdings[c] = holdings.get(c, 0) - rem / pv
                        if holdings[c] < 1e-8:
                            del holdings[c]
            if record is not None:
                nav = cash + sum(holdings.get(c, 0) * (df.loc[d, c] if not np.isnan(df.loc[d, c]) else 0) for c in holdings)
                row = {"date": d}
                if nav > 0:
                    for c in holdings:
                        pv = df.loc[d, c]
                        if not np.isnan(pv):
                            row[c] = holdings[c] * pv / nav
                record.append(row)
        # ── 事件止损：月度屏幕日重置峰值，其余交易日收盘盯持仓 ──
        if stop_active:
            if d in mf:
                for c in holdings:
                    pv = df.loc[d, c]
                    if not np.isnan(pv):
                        peak[c] = pv
            elif holdings:
                to_stop = []
                for c in list(holdings):
                    pv = df.loc[d, c]
                    if np.isnan(pv):
                        continue
                    pk = peak.get(c)
                    if pk is None:
                        peak[c] = pv
                        continue
                    if pv > pk:
                        peak[c] = pv                      # 新高 → 吊灯线上移
                        continue
                    dd = 1 - pv / pk
                    why = None
                    if stop_floor is not None and stop_floor > 0 and dd > stop_floor:
                        why = "floor"
                    elif stop_band is not None:
                        h = df.loc[:d, c].dropna()
                        a20 = atr_frac(h)
                        if not np.isnan(a20) and dd > stop_band * a20:
                            why = "band"
                    if why is not None:
                        to_stop.append((c, pv, dd, why))
                if to_stop:
                    stopped = [c for c, *_ in to_stop]
                    for c, pv, dd, why in to_stop:
                        freed = holdings[c] * pv * (1 - slip) * (1 - comm)
                        cash += freed
                        del holdings[c]
                        del peak[c]
                        # 混合归宿：有强下位（score>0且过波动过滤、非本人非持仓）才轮入
                        a = None
                        if rotate:
                            p2 = dates[dates < d]
                            if len(p2):
                                pv0 = p2[-1]
                                best, best_s = None, -1e18
                                for cc in df.columns:
                                    if cc in stopped or cc in holdings:
                                        continue
                                    hh = df.loc[:pv0, cc].dropna()
                                    if wave_max is not None:
                                        v = vol_ann(hh, lookback)
                                        if not np.isnan(v) and v > wave_max:
                                            continue
                                    s = momentum_score(hh, mom)
                                    if s > 0 and s > best_s:
                                        best, best_s = cc, s
                                a = best
                        if a is not None:
                            pva = df.loc[d, a]
                            if not np.isnan(pva):
                                tgt = freed * (1 - slip)
                                cost = tgt * (1 + comm)
                                if cost <= cash + 1e-9:
                                    holdings[a] = holdings.get(a, 0) + tgt / pva
                                    cash -= cost
                                    peak[a] = pva
                        if events is not None:
                            events.append({"date": d, "code": c, "dd%": round(dd * 100, 2),
                                           "reason": why,
                                           "after": ("rotate:" + a) if a is not None else "cash"})
        hv = sum(holdings.get(c, 0) * (df.loc[d, c] if not np.isnan(df.loc[d, c]) else 0) for c in holdings)
        eq[d] = cash + hv
    return pd.Series(eq).sort_index()


def segment_table(eq, end=None):
    """分年段统计：2021-23 / 2024-25 / 2026(至今) / 全段。返回 (表DataFrame, 净值列各段末值)。
    与 verify_filters.py 的 run() 同口径：首段以10万计收益，其余段接前段末净值。"""
    if end is None:
        end = eq.index[-1]
    end = pd.Timestamp(end)
    periods = [("2021-23", "2021-01-01", "2023-12-31"),
               ("2024-25", "2024-01-01", "2025-12-31"),
               ("2026", "2026-01-01", str(end.date())),
               ("全段", str(eq.index[0].date()), str(end.date()))]
    rows = []
    for name, s0, s1 in periods:
        seg = eq[(eq.index >= pd.Timestamp(s0)) & (eq.index <= pd.Timestamp(s1))]
        if len(seg) < 2:
            continue
        pre = eq[eq.index < pd.Timestamp(s0)]
        base = 100000.0 if len(pre) == 0 else pre.iloc[-1]
        ret = seg.iloc[-1] / base - 1
        yrs = (seg.index[-1] - seg.index[0]).days / 365.25
        ann = (seg.iloc[-1] / base) ** (1 / yrs) - 1 if yrs > 0.1 else ret
        peak = seg.cummax()
        mdd = (seg / peak - 1).min()
        r = seg.pct_change().dropna()
        shp = (r.mean() * 250 - 0.02) / (r.std() * math.sqrt(250)) if r.std() > 0 else 0
        rows.append({"区间": name, "收益%": ret * 100, "年化%": ann * 100,
                     "最大回撤%": mdd * 100, "夏普": shp})
    return pd.DataFrame(rows)
