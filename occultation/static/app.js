/* 掩星弦线拟合台 —— 前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (url, method = "GET", body) => {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error || ("HTTP " + r.status));
  }
  return r.json();
};

const state = {
  events: [],
  event: null,      // 当前事件完整状态
  fit: null,        // 当前拟合结果
  snapshots: [],
};

/* ---------------- 时间格式 ---------------- */
function parseTime(s) {
  const m = /^\s*(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)\s*$/.exec(s || "");
  if (!m) return null;
  return (+m[1]) * 3600 + (+m[2]) * 60 + parseFloat(m[3]);
}
function fmtTime(sec) {
  if (sec == null) return "—";
  sec = ((sec % 86400) + 86400) % 86400;
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}
const fmt = (x, n = 2) => (x == null || Number.isNaN(x)) ? "—" : (+x).toFixed(n);

/* ---------------- 事件管理 ---------------- */
async function refreshEvents(selectId) {
  state.events = await api("/api/events");
  const sel = $("eventSelect");
  sel.innerHTML = "";
  for (const ev of state.events) {
    const o = document.createElement("option");
    o.value = ev.id;
    o.textContent = `${ev.name}（${ev.date || "未填日期"}）`;
    sel.appendChild(o);
  }
  if (selectId) sel.value = selectId;
  if (state.events.length) await loadEvent(+sel.value);
  else { state.event = null; state.fit = null; renderAll(); }
}

async function loadEvent(id) {
  state.event = await api(`/api/events/${id}`);
  state.snapshots = state.event.snapshots || [];
  // 参数面板
  $("inpVelocity").value = state.event.velocity;
  $("inpDirection").value = state.event.direction;
  $("selModel").value = state.event.model;
  $("rngOffset").value = state.event.time_offset;
  $("numOffset").value = state.event.time_offset;
  $("offsetVal").textContent = fmt(state.event.time_offset);
  $("btnExport").disabled = false;
  await refit();
}

async function refit() {
  if (!state.event) return;
  const id = state.event.id;
  state.fit = await api(`/api/events/${id}/fit`, "POST", {
    model: $("selModel").value,
    time_offset: parseFloat($("numOffset").value || "0"),
  });
  // 重新拉事件状态（快照列表、排除标记可能变化）
  state.event = await api(`/api/events/${id}`);
  state.snapshots = state.event.snapshots || [];
  renderAll();
}

/* ---------------- 渲染：表格 ---------------- */
function renderStations() {
  const tb = $("staTable").querySelector("tbody");
  tb.innerHTML = "";
  const sel = $("obsSta");
  sel.innerHTML = "";
  if (!state.event) return;
  for (const s of state.event.stations) {
    const tr = document.createElement("tr");
    if (s.excluded) tr.className = "excluded";
    tr.innerHTML = `<td>${s.name}</td><td>${fmt(s.lat, 4)}</td><td>${fmt(s.lon, 4)}</td>`;
    const tdEx = document.createElement("td");
    const chk = document.createElement("input");
    chk.type = "checkbox"; chk.checked = !!s.excluded;
    chk.onchange = async () => {
      await api(`/api/stations/${s.id}`, "PUT", { excluded: chk.checked ? 1 : 0 });
      await refit();
    };
    tdEx.appendChild(chk);
    const tdDel = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = "删";
    btn.onclick = async () => {
      if (confirm(`删除站点 ${s.name} 及其观测？`)) {
        await api(`/api/stations/${s.id}`, "DELETE");
        await refit();
      }
    };
    tdDel.appendChild(btn);
    tr.append(tdEx, tdDel);
    tb.appendChild(tr);
    const o = document.createElement("option");
    o.value = s.id; o.textContent = s.name;
    sel.appendChild(o);
  }
}

function renderObservations() {
  const tb = $("obsTable").querySelector("tbody");
  tb.innerHTML = "";
  if (!state.event) return;
  for (const o of state.event.observations) {
    const tr = document.createElement("tr");
    const times = o.kind === "positive"
      ? `${fmtTime(o.t1)} → ${fmtTime(o.t2)}`
      : fmtTime(o.t1);
    const errs = o.kind === "positive"
      ? `${fmt(o.err1)}/${fmt(o.err2)}` : fmt(o.err1);
    tr.innerHTML = `<td>${o.station_name}</td>
      <td>${o.kind === "positive" ? "正" : "负"}</td>
      <td>${times}</td><td>${errs}</td>`;
    const td = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = "删";
    btn.onclick = async () => {
      await api(`/api/observations/${o.id}`, "DELETE");
      await refit();
    };
    td.appendChild(btn);
    tr.appendChild(td);
    tb.appendChild(tr);
  }
}

/* ---------------- 渲染：拟合结果 ---------------- */
function renderFitSummary() {
  const div = $("fitSummary");
  const conf = $("conflicts");
  conf.innerHTML = "";
  const f = state.fit;
  if (!f || !f.ok) {
    div.innerHTML = "<span class='hint'>尚无有效拟合（检查站点与观测是否足够）</span>";
    if (f) (f.warnings || []).forEach(w => {
      conf.innerHTML += `<div class="conflict">${w}</div>`;
    });
    renderResiduals();
    return;
  }
  const fit = f.fit;
  let rows = [
    ["模型", fit.model === "circle" ? "圆形" : "椭圆"],
    ["中心 (x, y) km", `(${fmt(fit.cx, 1)}, ${fmt(fit.cy, 1)}) ± (${fmt(fit.cx_sig, 1)}, ${fmt(fit.cy_sig, 1)})`],
    ["等效直径 km", `${fmt(fit.diameter_equiv, 1)}`],
    ["参与弦线", `${fit.n_chords} 条`],
    ["χ²/自由度", `${fmt(fit.chi2, 2)} / ${fit.dof}（约化 ${fmt(fit.chi2_red, 2)}）`],
    ["时间偏移", `${fmt(f.time_offset)} s`],
  ];
  if (fit.model === "circle") {
    rows.splice(2, 0, ["半径 km", `${fmt(fit.R, 1)} ± ${fmt(fit.R_sig, 1)}`]);
  } else {
    rows.splice(2, 0,
      ["半长轴 a km", `${fmt(fit.a, 1)} ± ${fmt(fit.a_sig, 1)}`],
      ["半短轴 b km", `${fmt(fit.b, 1)} ± ${fmt(fit.b_sig, 1)}`],
      ["长轴方位角 °", `${fmt(fit.phi, 1)} ± ${fmt(fit.phi_sig, 1)}`],
      ["扁率", fmt(fit.flattening, 3)]);
  }
  div.innerHTML = rows.map(([k, v]) => `<span><b>${k}</b></span><span>${v}</span>`).join("");

  if (f.conflicts.length) {
    f.conflicts.forEach(c => conf.innerHTML += `<div class="conflict">⚠ ${c}</div>`);
  } else if (f.negatives.length) {
    conf.innerHTML = `<div class="conflict ok">✓ 全部 ${f.negatives.length} 个负观测均在轮廓之外，无冲突</div>`;
  }
  renderResiduals();
}

function renderResiduals() {
  const tb = $("resTable").querySelector("tbody");
  tb.innerHTML = "";
  if (!state.fit) return;
  for (const r of state.fit.residuals || []) {
    const tr = document.createElement("tr");
    if (r.excluded) tr.className = "excluded";
    if (r.flag) tr.classList.add("flagged");
    tr.innerHTML = `<td>${r.station}</td>
      <td>${fmt(r.res_mid_km)}</td><td>${fmt(r.res_mid_sig)}</td>
      <td>${fmt(r.res_len_km)}</td><td>${fmt(r.res_len_sig)}</td>
      <td>${r.excluded ? "已排除" : (r.flag || "")}</td>`;
    tb.appendChild(tr);
  }
}

/* ---------------- 画布 ---------------- */
function computeView(result, W, H) {
  const pts = [];
  const d = result.d || [1, 0];
  for (const c of result.chords || []) {
    const L = c.half_len + (c.sig_len || 0);
    pts.push([c.qx - d[0] * L, c.qy - d[1] * L], [c.qx + d[0] * L, c.qy + d[1] * L]);
  }
  for (const n of result.negatives || []) pts.push([n.qx, n.qy]);
  if (result.fit) {
    const f = result.fit, R = Math.max(f.a || f.R, f.b || f.R);
    pts.push([f.cx - R, f.cy - R], [f.cx + R, f.cy + R]);
  }
  if (!pts.length) pts.push([-100, -100], [100, 100]);
  let x0 = Math.min(...pts.map(p => p[0])), x1 = Math.max(...pts.map(p => p[0]));
  let y0 = Math.min(...pts.map(p => p[1])), y1 = Math.max(...pts.map(p => p[1]));
  const mx = (x1 - x0) * 0.12 + 5, my = (y1 - y0) * 0.12 + 5;
  x0 -= mx; x1 += mx; y0 -= my; y1 += my;
  const s = Math.min(W / (x1 - x0), H / (y1 - y0));
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return {
    s,
    toX: (x) => W / 2 + (x - cx) * s,
    toY: (y) => H / 2 - (y - cy) * s,
    toKm: (px) => px / s,
  };
}

function drawScene(canvas, result, opts = {}) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#0d1b2a";
  ctx.fillRect(0, 0, W, H);
  const v = computeView(result, W, H);

  // 网格（每 50 km）
  ctx.strokeStyle = "rgba(120,150,190,.18)";
  ctx.fillStyle = "rgba(150,175,205,.55)";
  ctx.font = "10px sans-serif";
  ctx.lineWidth = 1;
  const step = 50;
  const inv = viewInverse(v, W, H);
  const lo = inv(0, H), hi = inv(W, 0);
  for (let gx = Math.floor(lo[0] / step) * step; gx <= hi[0]; gx += step) {
    ctx.beginPath();
    ctx.moveTo(v.toX(gx), 0); ctx.lineTo(v.toX(gx), H); ctx.stroke();
    ctx.fillText(`${gx}`, v.toX(gx) + 2, H - 4);
  }
  for (let gy = Math.floor(lo[1] / step) * step; gy <= hi[1]; gy += step) {
    ctx.beginPath();
    ctx.moveTo(0, v.toY(gy)); ctx.lineTo(W, v.toY(gy)); ctx.stroke();
    ctx.fillText(`${gy}`, 4, v.toY(gy) - 3);
  }

  const d = result.d || [1, 0], nrm = result.n || [1, 0];

  // 运动方向箭头
  const ax = W - 70, ay = 50;
  ctx.strokeStyle = "#8fb8e8"; ctx.fillStyle = "#8fb8e8";
  ctx.beginPath();
  ctx.moveTo(ax - d[0] * 25, ay + d[1] * 25);
  ctx.lineTo(ax + d[0] * 25, ay - d[1] * 25);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(ax + d[0] * 25, ay - d[1] * 25, 3, 0, 7);
  ctx.fill();
  ctx.fillText("影子运动", ax - 26, ay + 42);

  // 弦线
  for (const c of result.chords || []) {
    const col = c.excluded ? "rgba(140,150,160,.55)" : "#ffd166";
    const x1 = v.toX(c.qx - d[0] * c.half_len), y1 = v.toY(c.qy - d[1] * c.half_len);
    const x2 = v.toX(c.qx + d[0] * c.half_len), y2 = v.toY(c.qy + d[1] * c.half_len);
    // 不确定区间：端点 ±σ_len 的浅色延伸
    if (!c.excluded && (c.sig_len || 0) > 0) {
      const sl = c.sig_len;
      ctx.strokeStyle = "rgba(255,209,102,.35)";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(v.toX(c.qx - d[0] * (c.half_len + sl)), v.toY(c.qy - d[1] * (c.half_len + sl)));
      ctx.lineTo(x1, y1);
      ctx.moveTo(x2, y2);
      ctx.lineTo(v.toX(c.qx + d[0] * (c.half_len + sl)), v.toY(c.qy + d[1] * (c.half_len + sl)));
      ctx.stroke();
    }
    ctx.strokeStyle = col;
    ctx.lineWidth = c.excluded ? 1.5 : 3;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    // 中点及其中点不确定（±σ_mid 沿弦方向的小垂tick）
    ctx.fillStyle = col;
    ctx.beginPath();
    ctx.arc(v.toX(c.qx), v.toY(c.qy), 2.5, 0, 7);
    ctx.fill();
    if (!c.excluded && (c.sig_mid || 0) > 0) {
      ctx.strokeStyle = col; ctx.lineWidth = 1;
      for (const sgn of [-1, 1]) {
        const mx = v.toX(c.qx + sgn * d[0] * c.sig_mid);
        const my = v.toY(c.qy + sgn * d[1] * c.sig_mid);
        ctx.beginPath();
        ctx.moveTo(mx - nrm[0] * 4, my + nrm[1] * 4);
        ctx.lineTo(mx + nrm[0] * 4, my - nrm[1] * 4);
        ctx.stroke();
      }
    }
    // 站名
    ctx.fillStyle = c.excluded ? "rgba(160,170,180,.7)" : "#cfe3ff";
    ctx.font = "11px sans-serif";
    ctx.fillText(c.station, x2 + 5, y2 - 4);
    c._px = [x1, y1, x2, y2]; // 供点击命中
  }

  // 负观测
  for (const n of result.negatives || []) {
    const x = v.toX(n.qx), y = v.toY(n.qy);
    const conflict = n.inside && !n.excluded;
    const col = n.excluded ? "rgba(140,150,160,.6)" : conflict ? "#ff5c5c" : "#5cd68a";
    ctx.strokeStyle = col; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 6, 0, 7); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x - 4.3, y + 4.3); ctx.lineTo(x + 4.3, y - 4.3);
    ctx.stroke();
    ctx.fillStyle = col;
    ctx.font = "11px sans-serif";
    ctx.fillText(n.station + (conflict ? " ⚠冲突" : ""), x + 9, y + 4);
  }

  // 拟合轮廓
  if (result.fit) {
    const f = result.fit;
    ctx.strokeStyle = "#4dd0e1"; ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.beginPath();
    if (f.model === "circle") {
      ctx.arc(v.toX(f.cx), v.toY(f.cy), f.R * v.s, 0, 7);
    } else {
      const ph = f.phi * Math.PI / 180;
      const A = [Math.sin(ph), Math.cos(ph)], B = [Math.cos(ph), -Math.sin(ph)];
      for (let i = 0; i <= 120; i++) {
        const t = i / 120 * 2 * Math.PI;
        const x = f.cx + f.a * Math.cos(t) * A[0] + f.b * Math.sin(t) * B[0];
        const y = f.cy + f.a * Math.cos(t) * A[1] + f.b * Math.sin(t) * B[1];
        const px = v.toX(x), py = v.toY(y);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      }
      ctx.closePath();
    }
    ctx.stroke();
    // 中心十字
    ctx.strokeStyle = "#4dd0e1"; ctx.lineWidth = 1.5;
    const cxp = v.toX(f.cx), cyp = v.toY(f.cy);
    ctx.beginPath();
    ctx.moveTo(cxp - 7, cyp); ctx.lineTo(cxp + 7, cyp);
    ctx.moveTo(cxp, cyp - 7); ctx.lineTo(cxp, cyp + 7);
    ctx.stroke();
  }

  // 比例尺
  const barKm = 50;
  const bx = 20, by = H - 20;
  ctx.strokeStyle = "#cfe3ff"; ctx.fillStyle = "#cfe3ff"; ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(bx, by); ctx.lineTo(bx + barKm * v.s, by); ctx.stroke();
  ctx.font = "11px sans-serif";
  ctx.fillText(`${barKm} km`, bx + 4, by - 5);

  if (opts.title) {
    ctx.fillStyle = "#eaf2ff";
    ctx.font = "13px sans-serif";
    ctx.fillText(opts.title, 12, 20);
  }
  return v;
}

function viewInverse(v, W, H) {
  // 由 toX/toY 的线性性反推 km 坐标
  const xA = v.toX(0), xB = v.toX(1);
  const yA = v.toY(0), yB = v.toY(1);
  return (px, py) => [(px - xA) / (xB - xA), (py - yA) / (yB - yA)];
}

/* ---------------- 快照 ---------------- */
function renderSnapshots() {
  const ul = $("snapList");
  ul.innerHTML = "";
  const selA = $("cmpA"), selB = $("cmpB");
  selA.innerHTML = ""; selB.innerHTML = "";
  for (const s of state.snapshots) {
    const li = document.createElement("li");
    const t = new Date(s.created_at * 1000).toLocaleTimeString();
    li.innerHTML = `<span>#${s.id} ${s.label} <span class="badge">${t}</span></span>`;
    const btn = document.createElement("button");
    btn.textContent = "删";
    btn.onclick = async () => {
      await api(`/api/snapshots/${s.id}`, "DELETE");
      await refit();
    };
    li.appendChild(btn);
    ul.appendChild(li);
    for (const sel of [selA, selB]) {
      const o = document.createElement("option");
      o.value = s.id; o.textContent = `#${s.id} ${s.label}`;
      sel.appendChild(o);
    }
  }
  if (state.snapshots.length >= 2) {
    selA.value = state.snapshots[state.snapshots.length - 1].id;
    selB.value = state.snapshots[0].id;
  }
}

async function compareSnapshots() {
  const a = $("cmpA").value, b = $("cmpB").value;
  if (!a || !b) return alert("请先保存至少两个快照");
  const [sa, sb] = await Promise.all([
    api(`/api/snapshots/${a}`), api(`/api/snapshots/${b}`)]);
  $("cmpTitleA").textContent = `A：#${sa.id} ${sa.label}`;
  $("cmpTitleB").textContent = `B：#${sb.id} ${sb.label}`;
  $("cmpHeadA").textContent = `A #${sa.id}`;
  $("cmpHeadB").textContent = `B #${sb.id}`;
  drawScene($("cmpCanvasA"), sa.payload.fit_result, { title: sa.label });
  drawScene($("cmpCanvasB"), sb.payload.fit_result, { title: sb.label });

  const pa = sa.payload, pb = sb.payload;
  const fa = pa.fit_result.fit || {}, fb = pb.fit_result.fit || {};
  const rowsDef = [
    ["模型", pa.fit_result.model, pb.fit_result.model],
    ["时间偏移 s", pa.fit_result.time_offset, pb.fit_result.time_offset],
    ["影子速度 km/s", pa.event.velocity, pb.event.velocity],
    ["运动方向 °", pa.event.direction, pb.event.direction],
    ["参与弦线", fa.n_chords, fb.n_chords],
    ["中心 x km", fa.cx, fb.cx],
    ["中心 y km", fa.cy, fb.cy],
    ["等效直径 km", fa.diameter_equiv, fb.diameter_equiv],
    ["半长轴 a km", fa.a, fb.a],
    ["半短轴 b km", fa.b, fb.b],
    ["长轴方位角 °", fa.phi, fb.phi],
    ["约化 χ²", fa.chi2_red, fb.chi2_red],
    ["负观测冲突数", pa.fit_result.conflicts.length, pb.fit_result.conflicts.length],
    ["排除站点",
      pa.stations.filter(s => s.excluded).map(s => s.name).join(",") || "无",
      pb.stations.filter(s => s.excluded).map(s => s.name).join(",") || "无"],
  ];
  const tb = $("cmpTable").querySelector("tbody");
  tb.innerHTML = "";
  for (const [k, va, vb] of rowsDef) {
    let diff = "—";
    if (typeof va === "number" && typeof vb === "number") diff = fmt(vb - va, 2);
    else if (va !== vb) diff = "不同";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${k}</td><td>${typeof va === "number" ? fmt(va, 2) : va ?? "—"}</td>
      <td>${typeof vb === "number" ? fmt(vb, 2) : vb ?? "—"}</td><td>${diff}</td>`;
    tb.appendChild(tr);
  }
  $("cmpModal").classList.remove("hidden");
}

/* ---------------- 渲染总入口 ---------------- */
function renderAll() {
  renderStations();
  renderObservations();
  renderFitSummary();
  renderSnapshots();
  if (state.fit) {
    const title = state.event
      ? `${state.event.name} — ${state.fit.model === "circle" ? "圆形" : "椭圆"}拟合`
      : "";
    state._view = drawScene($("mainCanvas"), state.fit, { title });
  } else {
    const ctx = $("mainCanvas").getContext("2d");
    ctx.fillStyle = "#0d1b2a";
    ctx.fillRect(0, 0, 900, 640);
    ctx.fillStyle = "#8fb8e8";
    ctx.font = "15px sans-serif";
    ctx.fillText("请新建或选择一个观测事件", 30, 40);
  }
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("eventSelect").onchange = (e) => loadEvent(+e.target.value);

  $("btnNewEvent").onclick = () => $("evtModal").classList.remove("hidden");
  $("btnCancelEvt").onclick = () => $("evtModal").classList.add("hidden");
  $("btnCreateEvt").onclick = async () => {
    const name = $("evtName").value.trim();
    if (!name) return alert("请填写事件名称");
    const ev = await api("/api/events", "POST", {
      name,
      star: $("evtStar").value.trim(),
      date: $("evtDate").value.trim(),
      velocity: parseFloat($("evtVel").value || "20"),
      direction: parseFloat($("evtDir").value || "90"),
    });
    $("evtModal").classList.add("hidden");
    await refreshEvents(ev.id);
  };

  $("btnApplyParams").onclick = async () => {
    if (!state.event) return;
    await api(`/api/events/${state.event.id}`, "PUT", {
      velocity: parseFloat($("inpVelocity").value || "0"),
      direction: parseFloat($("inpDirection").value || "0"),
      model: $("selModel").value,
      time_offset: parseFloat($("numOffset").value || "0"),
    });
    await refit();
  };
  $("selModel").onchange = () => $("btnApplyParams").click();

  // 时间偏移：拖动即时重拟合（防抖）
  let timer = null;
  const offsetChanged = (val) => {
    $("numOffset").value = val;
    $("offsetVal").textContent = fmt(val);
    clearTimeout(timer);
    timer = setTimeout(refit, 180);
  };
  $("rngOffset").oninput = (e) => offsetChanged(e.target.value);
  $("numOffset").onchange = (e) => {
    $("rngOffset").value = e.target.value;
    offsetChanged(e.target.value);
  };

  $("btnAddSta").onclick = async () => {
    if (!state.event) return alert("请先选择事件");
    const name = $("staName").value.trim();
    const lat = parseFloat($("staLat").value), lon = parseFloat($("staLon").value);
    if (!name || isNaN(lat) || isNaN(lon)) return alert("请完整填写站名与经纬度");
    await api(`/api/events/${state.event.id}/stations`, "POST", { name, lat, lon });
    $("staName").value = $("staLat").value = $("staLon").value = "";
    await refit();
  };

  $("obsKind").onchange = (e) => {
    $("posFields").classList.toggle("hidden", e.target.value !== "positive");
    $("negFields").classList.toggle("hidden", e.target.value !== "negative");
  };
  $("btnAddObs").onclick = async () => {
    if (!state.event) return alert("请先选择事件");
    const sid = +$("obsSta").value;
    if (!sid) return alert("请先添加站点");
    const kind = $("obsKind").value;
    let payload;
    if (kind === "positive") {
      const t1 = parseTime($("obsT1").value), t2 = parseTime($("obsT2").value);
      if (t1 == null || t2 == null) return alert("时刻格式应为 HH:MM:SS.s");
      if (t2 <= t1) return alert("复现时刻应晚于消失时刻");
      payload = { kind, t1, t2,
        err1: parseFloat($("obsE1").value || "0"),
        err2: parseFloat($("obsE2").value || "0") };
    } else {
      const t1 = parseTime($("obsTN").value);
      if (t1 == null) return alert("时刻格式应为 HH:MM:SS.s");
      payload = { kind, t1, err1: parseFloat($("obsEN").value || "0") };
    }
    await api(`/api/stations/${sid}/observations`, "POST", payload);
    $("obsT1").value = $("obsT2").value = $("obsTN").value = "";
    await refit();
  };

  $("btnImport").onclick = async () => {
    if (!state.event) return alert("请先选择事件");
    const text = $("csvText").value;
    if (!text.trim()) return;
    const r = await api(`/api/events/${state.event.id}/import`, "POST", { text });
    $("importMsg").textContent =
      `导入：站点 ${r.added.stations}，正观测 ${r.added.positive}，负观测 ${r.added.negative}` +
      (r.errors.length ? "；错误：" + r.errors.join("；") : "");
    await refit();
  };
  $("btnDemo").onclick = async () => {
    if (!state.event) return alert("请先选择事件");
    if (!confirm("示例数据将替换当前事件的全部站点与观测，继续？")) return;
    const r = await api(`/api/events/${state.event.id}/demo`, "POST");
    $("importMsg").textContent =
      `已载入示例：正观测 ${r.positive}，负观测 ${r.negative}` +
      `（真实轮廓 a=${r.truth.a} b=${r.truth.b} φ=${r.truth.phi}°）`;
    await loadEvent(state.event.id);
  };

  $("btnExport").onclick = () => {
    if (state.event) window.open(`/api/events/${state.event.id}/export`, "_blank");
  };

  $("btnSaveSnap").onclick = async () => {
    if (!state.event) return;
    await api(`/api/events/${state.event.id}/snapshots`, "POST", {
      label: $("snapLabel").value.trim() || undefined,
    });
    $("snapLabel").value = "";
    await refit();
  };
  $("btnCompare").onclick = compareSnapshots;
  $("btnCloseCmp").onclick = () => $("cmpModal").classList.add("hidden");

  // 点击弦线切换排除
  $("mainCanvas").addEventListener("click", async (e) => {
    if (!state.fit) return;
    const rect = e.target.getBoundingClientRect();
    const scaleX = e.target.width / rect.width;
    const px = (e.clientX - rect.left) * scaleX;
    const py = (e.clientY - rect.top) * (e.target.height / rect.height);
    let best = null, bestD = 12; // 命中阈值 px
    for (const c of state.fit.chords || []) {
      if (!c._px) continue;
      const [x1, y1, x2, y2] = c._px;
      const dx = x2 - x1, dy = y2 - y1;
      const L2 = dx * dx + dy * dy || 1;
      let t = ((px - x1) * dx + (py - y1) * dy) / L2;
      t = Math.max(0, Math.min(1, t));
      const dist = Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
      if (dist < bestD) { bestD = dist; best = c; }
    }
    if (best) {
      const st = state.event.stations.find(s => s.id === best.station_id);
      await api(`/api/stations/${best.station_id}`, "PUT",
        { excluded: st.excluded ? 0 : 1 });
      await refit();
    }
  });
}

bind();
refreshEvents();
