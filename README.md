# ETF 动量轮动 + EPO · 策略网站

原池策略（13只ETF · 月度调仓 · 动量打分 + EPO优化）的公开网页版。
**GitHub Actions 每个交易日 16:30 自动跑 Python 回测引擎**，结果存成 JSON 提交进仓库；
**GitHub Pages 托管静态网页**展示。电脑不开机别人也能看，全免费。

## 网页上看什么

- 📅 本月首个交易日应持仓（真正的月度调仓口径）
- ⚡ 最新信号（数据截至最新交易日，仅供参考）
- 📈 策略 vs 沪深300 vs 上证指数 净值趋势图（可缩放）
- 📊 累计收益 / 年化 / 最大回撤 / 夏普 / 卡玛 / 波动 / 月度胜率 / 超额
- 🗂 分年段表现 + 每月调仓持仓表 + 全池动量排名

## 文件结构

```
etf-web/
├── app/                      # Python 计算层
│   ├── engine.py             # 回测引擎（与 本地回测/ 同一份，勿改算法）
│   ├── pools.py              # 策略池定义
│   ├── tencent_data.py       # 腾讯前复权数据源
│   └── build_results.py      # ★ 一键生成 docs/data/results.json
├── docs/                     # GitHub Pages 托管目录（纯静态）
│   ├── index.html / app.js / style.css
│   └── data/results.json     # 自动生成
└── .github/workflows/update.yml
```

## 本地运行

```bash
cd etf-web
python app/build_results.py      # 拉数据+回测，生成 docs/data/results.json（约2-3分钟）
cd docs
python -m http.server 8000       # 浏览器打开 http://localhost:8000
```

数据拉取失败（腾讯接口抽风）时**不会覆盖旧 results.json**，网页保持上次结果。

## 部署到 GitHub Pages（一次性）

1. 新建 GitHub 仓库，把这个 `etf-web/` 文件夹推上去（注意 `.github/` 是隐藏文件夹，要一起推）
2. 仓库 **Settings → Pages → Source 选 "Deploy from a branch" → 分支 main / 目录 `/docs`** → Save
3. Actions 页签手动触发一次「每日更新回测数据」验证能跑通
4. 之后每天北京时间 16:30 自动更新；网页地址 `https://<用户名>.github.io/<仓库名>/`

> 注意：仓库必须 **Public**（私有仓库的 Pages 要付费）。JSON 里只有回测数字，无敏感信息。

## 口径（与本地 backtest.py --pool S3 完全一致）

- 池：原池 13 只（黄金/豆粕/纳指华安159632/沪深300/创业板/创新药/新能车/消费/光伏/通信/计算机/军工/恒生科技），无波动过滤
- 动量：`Score = 年化收益 × R²`，34 天对数价格回归，score>0 才入选，取前3
- EPO anchored：w=0.2，逆方差 anchor，负权重裁0归一化
- 执行：每月首个交易日收盘调仓，滑点 0.2%，佣金 0.02%，起点 2021-01-01，初始 10 万
