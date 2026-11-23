// Cloudflare Worker: 网页按钮 -> GitHub Actions dispatch 中转
// 部署: wrangler deploy; 密钥: wrangler secret put GH_TOKEN
const REPO = "hylandcui-afk/hyland13etf";
const WF = "update.yml";
const ORIGIN = "https://hylandcui-afk.github.io";
const CORS = {
  "Access-Control-Allow-Origin": ORIGIN,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    if (url.pathname !== "/trigger" || request.method !== "POST")
      return new Response("not found", { status: 404, headers: CORS });
    if ((request.headers.get("Origin") || "") !== ORIGIN)
      return Response.json({ ok: false, error: "forbidden" }, { status: 403, headers: CORS });

    // 防滥用: 上一次运行 10 分钟内未结束，或 3 分钟内刚触发，则拒绝
    try {
      const runs = await fetch(
        "https://api.github.com/repos/" + REPO + "/actions/workflows/" + WF + "/runs?per_page=1",
        { headers: { "User-Agent": "refresh-button-worker" } }
      ).then(r => r.json());
      const last = runs.workflow_runs && runs.workflow_runs[0];
      if (last) {
        const age = Date.now() - Date.parse(last.created_at);
        if (last.status !== "completed" && age < 15 * 60 * 1000)
          return Response.json({ ok: false, reason: "run_in_progress" }, { headers: CORS });
        if (age < 3 * 60 * 1000)
          return Response.json({ ok: false, reason: "run_in_progress" }, { headers: CORS });
      }
    } catch (e) { /* 查询失败不阻断触发 */ }

    const resp = await fetch(
      "https://api.github.com/repos/" + REPO + "/actions/workflows/" + WF + "/dispatches",
      {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + env.GH_TOKEN,
          "User-Agent": "refresh-button-worker",
          "Accept": "application/vnd.github+json",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );
    if (resp.status === 204)
      return Response.json({ ok: true }, { headers: CORS });
    return Response.json({ ok: false, error: "dispatch_failed", status: resp.status },
      { status: 502, headers: CORS });
  },
};
