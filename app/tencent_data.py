"""
=============================================================================
  腾讯前复权 ETF 数据源 (tencent_data.py)
  ======================================
  干净的 ETF 历史日线数据：
    - 腾讯 qfq（前复权）：份额折算 / 分红已在源头平滑，无假崩盘
    - 分页拉取全历史：从上市日至今（约2011年起）
    - 替代新浪"不复权"数据源（新浪需要手动处理折算断点）

  接口：
    tencent_qfq(code, end='2026-08-14')          → pd.Series (date→close)
    tencent_qfq_all(codes, end='2026-08-14')     → pd.DataFrame (date→多列close)
    refresh_cache(csv_path, end='2026-08-14')    → 写入/刷新本地CSV缓存

  用法示例：
    import tencent_data as td
    s = td.tencent_qfq('sh510300')               # 单只全历史
    df = td.tencent_qfq_all(CODES)               # 多只合并
    td.refresh_cache('etf_cache.csv')            # 刷新缓存

  腾讯接口: web.ifzq.gtimg.cn/appstock/app/fqkline/get
    param = {code},day,{start},{end},{count},qfq
    - start 为起始日（设为 1900-01-01 从最早取）
    - end 为结束日，腾讯返回 [end往前count条]
    - count 单页上限 320 → 需分页：用返回首日作为下一页 end
  =============================================================================
"""
import os
import time
from datetime import datetime

import pandas as pd
import requests

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
PAGE = 320  # 腾讯单页最大条数

# 2025-26 推荐池（13只）：长期正期望 + 低相关分散
DEFAULT_CODES = [
    "sh.518880",  # 黄金ETF
    "sh.513100",  # 纳指ETF
    "sz.159915",  # 创业板ETF
    "sh.515880",  # 通信ETF（AI算力）
    "sz.159992",  # 创新药ETF
    "sz.159740",  # 恒生科技ETF
    "sz.159995",  # 芯片ETF
    "sh.512400",  # 有色金属ETF
    "sh.510880",  # 红利ETF
    "sh.511090",  # 30年国债ETF
    "sz.159892",  # 恒生医疗ETF
    "sz.159628",  # 北证50ETF
    "sh.562500",  # 机器人ETF
]


def _resolve_end(end):
    return end if end else datetime.now().strftime("%Y-%m-%d")


def _fetch_page(code: str, end: str, count: int = PAGE):
    """拉取 code 在 [最早, end] 内最近 count 条前复权日线。
    返回 [(date, open, close, high, low, vol), ...] 升序，None 表示失败。
    带重试：腾讯接口对高频请求会临时断连（RemoteDisconnected）。"""
    for attempt in range(4):
        symbol = code.replace(".", "")  # sh.510300 → sh510300
        seg = f"{symbol},day,1900-01-01,{end},{count},qfq"
        try:
            r = requests.get(TENCENT_URL, params={"param": seg}, timeout=15)
            j = r.json()
            d = j.get("data", {}).get(symbol)
            k = (d.get("qfqday") if isinstance(d, dict) else None) or None
            # qfq 请求下：有复权历史的返回 qfqday；从未分红/折算的只返回 day
            if k is None and isinstance(d, dict):
                k = d.get("day")
            if k:
                return list(k)
            time.sleep(1.0)
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def tencent_qfq(code: str, end: str = None) -> pd.Series:
    """拉取单只 ETF 全历史前复权收盘价（从上市日至今）。"""
    end = _resolve_end(end)
    rows = []
    e = end
    for _ in range(80):  # 80页 × 320条 = 25600天, 足够全历史
        page = _fetch_page(code, e)
        if not page:
            break
        rows = list(page) + rows
        if len(page) < PAGE:
            break  # 不足一页 = 已到最早
        e = page[0][0]  # 用返回首日继续往前翻
        time.sleep(0.5)
    if not rows:
        return pd.Series(dtype=float, name=code)
    ncol = len(rows[0])
    cols = ["date", "open", "close", "high", "low", "vol"]
    cols += [f"_c{i}" for i in range(max(0, ncol - len(cols)))]
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"])
    s = df.drop_duplicates("date").set_index("date")["close"].sort_index()
    s.name = code
    return s


def tencent_qfq_all(codes, end: str = None) -> pd.DataFrame:
    """拉取多只 ETF 全历史前复权收盘价，合并为一个 DataFrame（date → 多列）。"""
    end = _resolve_end(end)
    data = {}
    for i, code in enumerate(codes, 1):
        s = tencent_qfq(code, end)
        data[code] = s
        print(f"  [{i:>2}/{len(codes)}] {code}: {len(s)}条"
              f"{('  ' + str(s.index[0].date())) if len(s) else ''} ~ "
              f"{s.index[-1].date() if len(s) else '—'}")
        time.sleep(0.3)
    return pd.DataFrame(data).sort_index()


def refresh_cache(csv_path: str, codes=None, end: str = None) -> str:
    """拉取并写入前复权缓存 CSV。返回文件路径。"""
    if codes is None:
        codes = DEFAULT_CODES
    end = _resolve_end(end)
    print(f"📡 腾讯前复权拉取中（end={end}）...")
    df = tencent_qfq_all(codes, end)
    df.to_csv(csv_path, float_format="%.4f")
    print(f"💾 已写入 {csv_path}  ({len(df)}天, {len(df.columns)}列)")
    return csv_path


if __name__ == "__main__":
    # 测试：对比新浪不复权 vs 腾讯前复权在折算日的表现
    print("========== tencent_data 自测 ==========")
    test = {
        "sh513100": ("2022-01-14", "不复权当时-80.5%"),
        "sh510150": ("2021-05-31", "不复权当时-91.7%"),
        "sh515880": ("2026-02-03", "不复权当时-65.7%"),
        "sh515880": ("2026-07-06", "不复权当时-52.1%"),
    }
    for code, (d, note) in test.items():
        s = tencent_qfq(code)
        d0 = pd.Timestamp(d)
        if d0 in s.index:
            i = s.index.get_loc(d0)
            p, c = s.iloc[i - 1], s.iloc[i]
            print(f"  {code} {d}: 前复权 {(c / p - 1) * 100:+.2f}%  [{note}]")
