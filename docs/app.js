// 渲染 results.json → 页面。纯静态，无构建。
const pct = (v, digits = 1) => (v == null ? "—" : (v * 100).toFixed(digits) + "%");
const cls = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
const fmt = (v, d = 2) => (v == null ? "—" : v.toFixed(d));

function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; }

// ── 当前信号卡 ──
function renderTargets(containerId, asofId, block, rebalanceLabel) {
  const box = document.getElementById(containerId);
  box.innerHTML = "";
  const targets = block.targets || [];
  if (!targets.length) {
    box.appendChild(el(`<div class="empty">无正动量标的 → 空仓持有现金</div>`));
  } else {
    targets.forEach(t => {
      box.appendChild(el(`<div class="t-row"><span>${t.name} <span class="code">${t.code.split(".")[1]}</span></span><span class="w">${pct(t.weight)}</span></div>`));
    });
  }
  const asof = block.data_asof;
  document.getElementById(asofId).textContent =
    (block.rebalance_date ? `调仓日 ${block.rebalance_date} · ` : "") + `数据截至 ${asof}`;
}

// ── 指标卡 ──
function renderMetrics(m) {
  const items = [
    { k: "累计收益", v: pct(m.total_return), c: cls(m.total_return) },
    { k: "年化收益", v: pct(m.annual_return), c: cls(m.annual_return) },
    { k: "最大回撤", v: pct(m.max_drawdown), c: "down" },
    { k: "夏普", v: fmt(m.sharpe), c: cls(m.sharpe) },
    { k: "卡玛", v: fmt(m.calmar), c: cls(m.calmar) },
    { k: "年化波动", v: pct(m.annual_vol), c: "" },
    { k: "月度胜率", v: pct(m.monthly_win_rate, 0), c: "" },
    { k: "沪深300同期", v: pct(m.bench_hs300), c: cls(m.bench_hs300) },
    { k: "超额(vs沪深300)", v: pct(m.excess_hs300), c: cls(m.excess_hs300) },
  ];
  const sec = document.getElementById("metric-cards");
  items.forEach(it => {
    sec.appendChild(el(`<div class="card metric"><div class="v ${it.c}">${it.v}</div><div class="k">${it.k}</div></div>`));
  });
}

// ── 趋势图 ──
function renderChart(nav) {
  const chart = echarts.init(document.getElementById("chart"));
  const series = [
    { name: "策略", data: nav.strategy, color: "#f5a623", width: 2.4 },
    { name: "沪深300", data: nav.hs300, color: "#5b8ff9", width: 1.4 },
    { name: "上证指数", data: nav.shanghai, color: "#8b95a8", width: 1.4 },
  ];
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: v => (v * 100).toFixed(1) },
    legend: { data: series.map(s => s.name), textStyle: { color: "#8b95a8" }, top: 0 },
    grid: { left: 50, right: 20, top: 36, bottom: 30 },
    xAxis: { type: "category", data: nav.dates, axisLabel: { color: "#8b95a8" }, axisLine: { lineStyle: { color: "#2c3648" } } },
    yAxis: { type: "value", scale: true, axisLabel: { color: "#8b95a8", formatter: v => v.toFixed(1) }, splitLine: { lineStyle: { color: "#222c3e" } } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 2, borderColor: "#2c3648" }],
    series: series.map(s => ({ name: s.name, type: "line", data: s.data, showSymbol: false,
                              lineStyle: { width: s.width, color: s.color }, itemStyle: { color: s.color } })),
  });
  window.addEventListener("resize", () => chart.resize());
}

// ── 分年段表 ──
function renderSegments(segs) {
  const t = document.getElementById("seg-table");
  t.innerHTML = `<thead><tr><th>区间</th><th>收益</th><th>年化</th><th>最大回撤</th><th>夏普</th></tr></thead>`;
  const body = el("<tbody></tbody>");
  segs.forEach(s => {
    body.appendChild(el(`<tr>
      <td>${s["区间"]}</td>
      <td class="${cls(s["收益%"])}">${pct(s["收益%"] / 100)}</td>
      <td class="${cls(s["年化%"])}">${pct(s["年化%"] / 100)}</td>
      <td class="neg">${pct(s["最大回撤%"] / 100)}</td>
      <td class="${cls(s["夏普"])}">${fmt(s["夏普"])}</td>
    </tr>`));
  });
  t.appendChild(body);
}

// ── 动量排名表 ──
function renderRank(ranked) {
  const t = document.getElementById("rank-table");
  t.innerHTML = `<thead><tr><th>#</th><th>标的</th><th>动量分</th><th>入选</th></tr></thead>`;
  const body = el("<tbody></tbody>");
  const n = ranked.length;
  ranked.forEach((r, i) => {
    body.appendChild(el(`<tr>
      <td>${i + 1}</td>
      <td>${r.name} <span class="code">${r.code.split(".")[1]}</span></td>
      <td class="${cls(r.score)}">${r.score.toFixed(4)}</td>
      <td>${r.score > 0 && i < 3 ? '<span class="hold">✓ 前3持仓</span>' : ""}</td>
    </tr>`));
  });
  t.appendChild(body);
}

// ── 每月持仓表：行=调仓日，列=标的 ──
function renderHoldings(rows, names) {
  const t = document.getElementById("hold-table");
  if (!rows || !rows.length) { t.innerHTML = "<tbody><tr><td>（无记录）</td></tr></tbody>"; return; }
  // 收集所有出现过的代码
  const codes = [];
  rows.forEach(r => Object.keys(r).forEach(k => { if (k !== "date" && !codes.includes(k)) codes.push(k); }));
  const head = `<thead><tr><th>调仓日</th>${codes.map(c => `<th>${names[c] || c}</th>`).join("")}<th>现金</th></tr></thead>`;
  t.innerHTML = head;
  const body = el("<tbody></tbody>");
  rows.forEach(r => {
    let sum = 0;
    const cells = codes.map(c => {
      const w = r[c];
      if (w == null) return "<td>·</td>";
      sum += w;
      return `<td class="hold">${(w * 100).toFixed(1)}%</td>`;
    });
    const cash = Math.max(0, 1 - sum);
    body.appendChild(el(`<tr><td>${r.date instanceof String || typeof r.date === "string" ? r.date : JSON.stringify(r.date)}</td>${cells.join("")}<td>${cash > 0.001 ? (cash * 100).toFixed(1) + "%" : "·"}</td></tr>`));
  });
  t.appendChild(body);
}

// ── 主流程 ──
fetch("data/results.json", { cache: "no-store" })
  .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then(d => {
    const names = { ...(d.meta.names || {}) };   // 全池代码→名称
    d.current.month_first?.ranked?.forEach(r => names[r.code] = r.name);
    d.current.latest?.ranked?.forEach(r => names[r.code] = r.name);
    d.monthly_holdings?.forEach(row => Object.keys(row).forEach(k => { if (k !== "date" && !names[k]) names[k] = k; }));

    document.getElementById("meta-line").textContent =
      `数据截至 ${d.meta.data_end} · 生成于 ${d.meta.generated_at}`;
    renderTargets("month-first-targets", "month-first-asof", d.current.month_first);
    renderTargets("latest-targets", "latest-asof", d.current.latest);
    renderMetrics(d.metrics);
    renderChart(d.nav);
    renderSegments(d.segments);
    renderRank(d.current.latest?.ranked || []);
    renderHoldings(d.monthly_holdings, names);
    document.getElementById("footer-line").textContent =
      `数据截至 ${d.meta.data_end} · GitHub Actions 每个交易日 16:30 自动更新`;
  })
  .catch(e => {
    document.getElementById("meta-line").textContent = "❌ 加载 results.json 失败：" + e.message +
      "（本地预览请先跑 python app/build_results.py，再用 python -m http.server 打开，不要直接双击文件）";
  });
