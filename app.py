"""青年驿站监控 Web 控制台。

在网页上选择区域/日期等条件，点击「预览 / 启动 / 停止」控制后台监控。
通知(Bark 等)与 verify_ssl 沿用 config.yaml；网页只控制过滤条件与轮询。

运行:
  python app.py            # 默认监听 http://127.0.0.1:8765
"""

import copy
import threading
import time

from flask import Flask, jsonify, request

import monitor as M
from qnyz_client import QnyzClient

app = Flask(__name__)


# ── 后台监控线程管理 ──────────────────────────────────────
class MonitorRunner:
    """后台监控管理。

    用『代际(generation)』守卫处理重启：每次 start/stop 都递增 gen，
    旧线程发现 gen 变化后不再推送、不再写状态、不再更新界面状态，并结束循环。
    这样即使旧线程正卡在一次较慢的网络请求中，新条件也能立即生效，
    不会用旧筛选条件误推送（例如设置了日期窗口却收到窗口外的日期）。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.gen = 0                 # 当前期望代际
        self.stop_event = threading.Event()
        self.status = {
            "running": False,
            "started_at": None,
            "last_run": None,
            "last_available": 0,
            "last_new": 0,
            "interval": None,
            "error": None,
            "filters": None,
        }
        self.latest = {"count": 0, "stations": [], "time": None}

    def is_running(self):
        return bool(self.status.get("running"))

    def start(self, cfg):
        with self.lock:
            restarted = self.status.get("running", False)
            self.gen += 1               # 取代任何旧线程
            my_gen = self.gen
            self.stop_event.set()       # 让旧线程尽快退出循环
            self.stop_event = threading.Event()
            ev = self.stop_event
            self.latest = {"count": 0, "stations": [], "time": None}
            self.status.update({
                "running": True,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_run": None,
                "last_available": 0,
                "last_new": 0,
                "error": None,
                "interval": M.to_int(cfg.get("interval"), 300),
                "filters": cfg.get("filters"),
            })
            t = threading.Thread(target=self._loop, args=(cfg, my_gen, ev), daemon=True)
            t.start()
            return True, ("已按新条件重新启动" if restarted else "已启动")

    def stop(self):
        with self.lock:
            if not self.status.get("running"):
                return False, "监控未在运行"
            self.gen += 1               # 取代当前线程
            self.stop_event.set()
            self.status["running"] = False
            return True, "已停止"

    def _loop(self, cfg, my_gen, ev):
        interval = M.to_int(cfg.get("interval"), 300)
        gate = lambda: (self.gen == my_gen) and (not ev.is_set())
        try:
            client = QnyzClient(
                base_url=cfg.get("base_url", "https://qnyz.shyouth.net/qnyzApi"),
                verify=cfg.get("verify_ssl", True),
            )
            state = M.load_state()
            while gate():
                try:
                    results, new_hits = M.run_once(
                        client, cfg, state, do_notify=True, notify_gate=gate)
                    if not gate():
                        break            # 已被新配置取代，丢弃本轮结果
                    now = time.strftime("%Y-%m-%d %H:%M:%S")
                    self.latest = {
                        "count": len(results),
                        "stations": stations_to_json(results),
                        "time": now,
                    }
                    self.status.update({
                        "last_run": now,
                        "last_available": len(results),
                        "last_new": len(new_hits),
                        "error": None,
                    })
                except Exception as e:  # noqa: BLE001
                    if gate():
                        self.status["error"] = str(e)
                ev.wait(interval)
        finally:
            if self.gen == my_gen:
                self.status["running"] = False


def stations_to_json(results):
    """把 run_once 的 [(station, dates)] 转成前端表格用的 JSON，按可约总数降序。"""
    out = []
    for s, dates in results:
        out.append({
            "name": s.get("name"),
            "district": s.get("district"),
            "houseCount": s.get("houseCount"),
            "total": sum(n for _, n in dates) if dates else 0,
            "dates": [{"date": d, "num": n} for d, n in (dates or [])],
            "dizhi": s.get("dizhi"),
            "lxfs": s.get("lxfs"),
            "groupOnly": bool(s.get("groupOnly")),
        })
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


runner = MonitorRunner()


# ── 组装配置 ──────────────────────────────────────────────
def build_cfg(overrides):
    """以 config.yaml 为底，用网页参数覆盖 filters/interval。"""
    cfg = M.load_config(M.DEFAULT_CONFIG)
    filters = dict(cfg.get("filters") or {})
    o = overrides or {}
    if "districts" in o:
        filters["districts"] = [d for d in (o.get("districts") or []) if d]
    for k in ("name", "date_from", "date_to", "apply_scope"):
        if k in o:
            filters[k] = o.get(k) or ("all" if k == "apply_scope" else "")
    for k in ("min_apply_number", "min_house_count"):
        if k in o and o.get(k) not in (None, ""):
            filters[k] = M.to_int(o.get(k), filters.get(k))
    cfg["filters"] = filters
    if o.get("interval") not in (None, ""):
        cfg["interval"] = M.to_int(o.get("interval"), cfg.get("interval"))
    return cfg


# ── API ───────────────────────────────────────────────────
@app.get("/api/districts")
def api_districts():
    cfg = M.load_config(M.DEFAULT_CONFIG)
    client = QnyzClient(base_url=cfg.get("base_url", "https://qnyz.shyouth.net/qnyzApi"),
                        verify=cfg.get("verify_ssl", True))
    from collections import Counter
    stations = client.list_houses()
    counts = Counter(s.get("district") for s in stations if s.get("district"))
    return jsonify([{"name": d, "count": c} for d, c in counts.most_common()])


@app.get("/api/status")
def api_status():
    st = dict(runner.status)
    st["running"] = runner.is_running()
    return jsonify(st)


@app.post("/api/preview")
def api_preview():
    cfg = build_cfg(request.get_json(silent=True) or {})
    client = QnyzClient(base_url=cfg.get("base_url", "https://qnyz.shyouth.net/qnyzApi"),
                        verify=cfg.get("verify_ssl", True))
    # 预览：不发通知、不写状态（用状态副本）
    state_copy = copy.deepcopy(M.load_state())
    results, _ = M.run_once(client, cfg, state_copy, do_notify=False, persist=False)
    out = stations_to_json(results)
    return jsonify({"count": len(out), "stations": out})


@app.get("/api/latest")
def api_latest():
    """后台监控每轮的最新结果（供网页运行时自动刷新表格）。"""
    return jsonify(runner.latest)


@app.post("/api/start")
def api_start():
    cfg = build_cfg(request.get_json(silent=True) or {})
    ok, msg = runner.start(cfg)
    return jsonify({"ok": ok, "msg": msg, "filters": cfg.get("filters"),
                    "interval": cfg.get("interval")})


@app.post("/api/stop")
def api_stop():
    ok, msg = runner.stop()
    return jsonify({"ok": ok, "msg": msg})


@app.get("/")
def index():
    return INDEX_HTML


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>青年驿站监控控制台</title>
<style>
  :root { --bd:#e2e5ea; --pri:#2b6cb0; --ok:#2f855a; --stop:#c53030; --muted:#718096; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin:0; background:#f5f6f8; color:#1a202c; }
  header { background:#2b3a55; color:#fff; padding:14px 20px; font-size:18px; font-weight:600; }
  .wrap { max-width: 1000px; margin: 18px auto; padding: 0 16px; }
  .card { background:#fff; border:1px solid var(--bd); border-radius:10px; padding:16px 18px;
          margin-bottom:16px; }
  h3 { margin:0 0 12px; font-size:15px; }
  label { font-size:13px; color:#2d3748; }
  .row { display:flex; flex-wrap:wrap; gap:16px; align-items:flex-end; }
  .field { display:flex; flex-direction:column; gap:6px; }
  input[type=date], input[type=number], input[type=text] {
     padding:7px 9px; border:1px solid var(--bd); border-radius:7px; font-size:14px; }
  .districts { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px,1fr)); gap:6px; }
  .districts label { display:flex; align-items:center; gap:6px; padding:5px 7px;
     border:1px solid var(--bd); border-radius:7px; cursor:pointer; }
  .districts label:hover { background:#f0f4fa; }
  .btns { display:flex; gap:10px; flex-wrap:wrap; }
  button { border:0; border-radius:8px; padding:9px 18px; font-size:14px; cursor:pointer; color:#fff; }
  .b-prev { background:#4a5568; } .b-start { background:var(--ok); } .b-stop { background:var(--stop); }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .status { display:flex; gap:20px; flex-wrap:wrap; font-size:13px; }
  .status b { color:#2d3748; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
  .on { background:var(--ok); } .off { background:#a0aec0; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid #edf0f4; vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  .tag { background:#ebf4ff; color:var(--pri); border-radius:5px; padding:1px 6px; font-size:12px; }
  .muted { color:var(--muted); font-size:12px; }
  .toast { position:fixed; top:16px; right:16px; background:#2d3748; color:#fff; padding:10px 16px;
     border-radius:8px; font-size:13px; opacity:0; transition:.3s; }
  .toast.show { opacity:1; }
</style>
</head>
<body>
<header>青年驿站 · 房源监控控制台</header>
<div class="wrap">

  <div class="card">
    <h3>筛选条件</h3>
    <div class="row" style="margin-bottom:12px">
      <div class="field"><label>入住日期</label><input type="date" id="date_from"></div>
      <div class="field"><label>离店日期</label><input type="date" id="date_to"></div>
      <div class="field"><label>每日可约数 ≥</label><input type="number" id="min_apply_number" value="1" min="1" style="width:90px"></div>
      <div class="field"><label>名称关键字</label><input type="text" id="name" placeholder="如 友间" style="width:140px"></div>
      <div class="field"><label>申请类型</label>
        <select id="apply_scope" style="padding:7px 9px;border:1px solid var(--bd);border-radius:7px;font-size:14px">
          <option value="all">全部</option>
          <option value="personal">仅个人可申请</option>
          <option value="group">仅供集体申请</option>
        </select>
      </div>
      <div class="field"><label>轮询间隔(秒)</label><input type="number" id="interval" value="300" min="30" style="width:100px"></div>
    </div>
    <label>区域（不选=全部）</label>
    <div class="districts" id="districts" style="margin-top:8px"></div>
  </div>

  <div class="card">
    <div class="btns">
      <button class="b-prev" id="btnPreview">🔍 预览当前可约</button>
      <button class="b-start" id="btnStart">▶ 启动监控</button>
      <button class="b-stop" id="btnStop">⏹ 停止监控</button>
    </div>
    <div class="status" style="margin-top:14px" id="status"></div>
  </div>

  <div class="card">
    <h3>结果 <span class="muted" id="resultMeta"></span></h3>
    <div style="overflow:auto">
      <table id="resultTable">
        <thead><tr><th>驿站</th><th>区域</th><th>可约合计</th><th>可约日期</th><th>地址 / 电话</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
function toast(msg){ const t=$("#toast"); t.textContent=msg; t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"),2200); }

function selectedDistricts(){
  return [...document.querySelectorAll("#districts input:checked")].map(x=>x.value);
}
function params(){
  return {
    districts: selectedDistricts(),
    date_from: $("#date_from").value,
    date_to: $("#date_to").value,
    min_apply_number: $("#min_apply_number").value,
    name: $("#name").value,
    apply_scope: $("#apply_scope").value,
    interval: $("#interval").value,
  };
}
async function loadDistricts(){
  const r = await fetch("/api/districts"); const list = await r.json();
  $("#districts").innerHTML = list.map(d =>
    `<label><input type="checkbox" value="${d.name}">${d.name} <span class="muted">(${d.count})</span></label>`
  ).join("");
}
function renderStatus(st){
  const dot = st.running ? '<span class="dot on"></span>运行中' : '<span class="dot off"></span>已停止';
  $("#status").innerHTML = `
    <div>${dot}</div>
    <div><b>启动:</b> ${st.started_at||"-"}</div>
    <div><b>上次检查:</b> ${st.last_run||"-"}</div>
    <div><b>可约驿站:</b> ${st.last_available||0}</div>
    <div><b>本轮新增:</b> ${st.last_new||0}</div>
    <div><b>间隔:</b> ${st.interval? st.interval+"s":"-"}</div>
    ${st.error? '<div style="color:#c53030"><b>错误:</b> '+st.error+'</div>':''}`;
  $("#btnStart").disabled = st.running;
  $("#btnStop").disabled = !st.running;
}
async function refreshStatus(){
  try{
    const st = await (await fetch("/api/status")).json();
    renderStatus(st);
    // 监控运行时，自动把每轮最新结果刷进表格
    if (st.running) {
      const latest = await (await fetch("/api/latest")).json();
      if (latest.time && latest.time !== liveTime) {
        liveTime = latest.time;
        renderResults(latest, `🟢 实时 · ${latest.time} · 共 ${latest.count} 个驿站可约`);
      } else if (!latest.time && !liveTime) {
        $("#resultMeta").textContent = "⏳ 首轮查询中…（上游列表接口首次响应较慢，约 15–40 秒）";
      }
    } else {
      liveTime = null;
    }
  }catch(e){}
}
let liveTime = null;   // 已渲染的后台最新结果时间，避免重复渲染
function renderResults(data, meta){
  $("#resultMeta").textContent = meta || `共 ${data.count} 个驿站可约`;
  $("#resultTable tbody").innerHTML = data.stations.map(s => {
    const ds = s.dates.slice(0,10).map(d=>`${d.date}×${d.num}`).join("，") + (s.dates.length>10?" …":"");
    const badge = s.groupOnly
      ? ' <span class="tag" style="background:#fff5f5;color:#c53030">仅集体</span>' : '';
    return `<tr>
      <td>${s.name||""}${badge}</td>
      <td><span class="tag">${s.district||""}</span></td>
      <td><b>${s.total}</b> <span class="muted">/共${s.houseCount??"?"}</span></td>
      <td>${ds}</td>
      <td>${s.dizhi||""}<br><span class="muted">☎ ${s.lxfs||""}</span></td>
    </tr>`;
  }).join("");
}
$("#btnPreview").onclick = async () => {
  toast("查询中…");
  const r = await fetch("/api/preview",{method:"POST",headers:{"Content-Type":"application/json"},
    body: JSON.stringify(params())});
  renderResults(await r.json()); toast("预览完成");
};
$("#btnStart").onclick = async () => {
  const r = await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},
    body: JSON.stringify(params())});
  const j = await r.json(); toast(j.msg); refreshStatus();
};
$("#btnStop").onclick = async () => {
  const j = await (await fetch("/api/stop",{method:"POST"})).json(); toast(j.msg); refreshStatus();
};
loadDistricts(); refreshStatus(); setInterval(refreshStatus, 5000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-reload", dest="reload", action="store_false",
                    help="关闭代码自动重载")
    ap.set_defaults(reload=True)
    args = ap.parse_args()
    print(f"控制台: http://{args.host}:{args.port} (自动重载: {'开' if args.reload else '关'})")
    # 自动重载：修改 .py 后进程自动重启（不开交互式调试器）。
    # 注意：重启会重置后台监控状态，正在运行的监控会停止，需重新点「启动」。
    app.run(host=args.host, port=args.port, threaded=True,
            use_reloader=args.reload, use_debugger=False)
