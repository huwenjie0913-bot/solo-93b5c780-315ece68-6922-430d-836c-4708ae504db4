/* 光变曲线判读 —— 前端模块（依赖 app.js 提供的 api/$/fmt/fmtTime/state） */
"use strict";

const lcState = {
  curves: [],
  curve: null,        // 当前曲线完整状态（series + versions）
  view: null,         // {t0, t1} 当前时间窗口
  mode: "pan",        // pan | pre | post | mask
  baselines: { pre: null, post: null },
  masked: new Set(),
  degree: 1,
  norm: null,         // 归一化预览/拟合得到的 {y, err, base}
  showNorm: false,
  fit: null,          // 最近一次拟合结果
  cmp: new Set(),     // 参与比较的版本 id
  drag: null,
};

function fmtTime2(sec) {
  if (sec == null) return "—";
  sec = ((sec % 86400) + 86400) % 86400;
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
}

/* ---------------- 数据加载 ---------------- */
async function lcOpen() {
  if (!state.event) return;
  $("lcModal").classList.remove("hidden");
  const sel = $("lcStation");
  sel.innerHTML = "";
  for (const s of state.event.stations) {
    const o = document.createElement("option");
    o.value = s.id; o.textContent = s.name;
    sel.appendChild(o);
  }
  if (!state.event.stations.length) {
    $("lcImportMsg").textContent = "请先在主界面为该事件添加站点";
  }
  await lcLoadCurves();
}

async function lcLoadCurves(selectId) {
  const sid = +$("lcStation").value;
  lcState.curve = null; lcState.fit = null; lcState.norm = null;
  lcState.baselines = { pre: null, post: null };
  lcState.masked = new Set(); lcState.cmp = new Set();
  const sel = $("lcCurve");
  sel.innerHTML = "";
  if (!sid) { lcDraw(); lcDrawResid(); lcRenderVersions(); lcRenderResult(); lcRenderCompare(); return; }
  lcState.curves = await api(`/api/stations/${sid}/lightcurves`);
  for (const c of lcState.curves) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = `#${c.id} ${c.name}（${c.n_points} 点）`;
    sel.appendChild(o);
  }
  const id = selectId || (lcState.curves[0] && lcState.curves[0].id);
  if (id) { sel.value = id; await lcSelectCurve(id); }
  else { lcDraw(); lcDrawResid(); lcRenderVersions(); lcRenderResult(); lcRenderCompare(); }
}

async function lcSelectCurve(id) {
  lcState.curve = await api(`/api/lightcurves/${id}`);
  lcState.view = null;
  lcState.baselines = { pre: null, post: null };
  lcState.masked = new Set();
  lcState.fit = null; lcState.norm = null;
  lcState.cmp = new Set();
  $("lcFitNote").textContent = "";
  lcDraw(); lcDrawResid(); lcRenderVersions(); lcRenderResult(); lcRenderCompare();
}

function lcResetView() {
  const s = lcState.curve.series;
  const t0 = s.t[0], t1 = s.t[s.t.length - 1];
  const pad = (t1 - t0) * 0.03 || 1;
  lcState.view = { t0: t0 - pad, t1: t1 + pad };
}

function lcSettings() {
  return {
    baselines: { pre: lcState.baselines.pre, post: lcState.baselines.post },
    masked: [...lcState.masked].sort((a, b) => a - b),
    degree: lcState.degree,
  };
}

/* 设置变化：作废旧拟合与预览 */
function lcSettingsChanged() {
  lcState.fit = null;
  lcState.norm = null;
  $("lcFitNote").textContent = "设置已更改，请重新拟合";
  if (lcState.showNorm) lcPreview();
  else { lcDraw(); lcDrawResid(); }
  lcRenderResult();
}

async function lcPreview() {
  if (!lcState.curve) return;
  try {
    const r = await api(`/api/lightcurves/${lcState.curve.id}/preview`, "POST", lcSettings());
    lcState.norm = { y: r.norm.y, err: r.norm.err, base: r.norm.base };
    $("lcWarn").innerHTML = "";
  } catch (ex) {
    lcState.norm = null;
    lcState.showNorm = false;
    $("lcShowNorm").checked = false;
    $("lcWarn").innerHTML = `<div class="conflict">✗ ${ex.message}</div>`;
  }
  lcDraw(); lcDrawResid();
}

async function lcFit(note) {
  if (!lcState.curve) return;
  if (!lcState.baselines.pre && !lcState.baselines.post) {
    alert("请先用“框选前基线 / 框选后基线”模式在图上框选基线区间");
    return;
  }
  let f;
  try {
    f = await api(`/api/lightcurves/${lcState.curve.id}/fit`, "POST", lcSettings());
  } catch (ex) {
    $("lcWarn").innerHTML = `<div class="conflict">✗ ${ex.message}</div>`;
    return;
  }
  lcState.fit = f;
  if (f.y) {
    lcState.norm = { y: f.y, err: f.yerr, base: f.base };
    lcState.showNorm = true;
    $("lcShowNorm").checked = true;
  }
  $("lcFitNote").textContent = note || "";
  lcDraw(); lcDrawResid(); lcRenderResult();
}

/* ---------------- 画布 ---------------- */
const LC_L = 58, LC_R = 12, LC_T = 12, LC_B = 24;

function lcMapFor(canvas, t0, t1, y0, y1) {
  const pw = canvas.width - LC_L - LC_R, ph = canvas.height - LC_T - LC_B;
  return {
    pw, ph,
    X: (t) => LC_L + (t - t0) / (t1 - t0) * pw,
    Y: (v) => LC_T + (y1 - v) / (y1 - y0) * ph,
    T: (px) => t0 + (px - LC_L) / pw * (t1 - t0),
    V: (py) => y1 - (py - LC_T) / ph * (y1 - y0),
  };
}

function lcY(i) {
  const s = lcState.curve.series;
  return (lcState.showNorm && lcState.norm) ? lcState.norm.y[i] : s.flux[i];
}
function lcYerr(i) {
  const s = lcState.curve.series;
  return (lcState.showNorm && lcState.norm) ? lcState.norm.err[i] : s.err[i];
}

function lcBaseAt(t) {
  const b = lcState.fit && lcState.fit.baseline;
  if (!b) return 1;
  let v = 0;
  for (let k = 0; k < b.coeff.length; k++) v += b.coeff[k] * Math.pow(t - b.t_ref, k);
  return v;
}

function lcDraw() {
  const cv = $("lcCanvas");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.fillStyle = "#0d1b2a";
  ctx.fillRect(0, 0, W, H);
  const cur = lcState.curve;
  if (!cur) {
    ctx.fillStyle = "#8fb8e8"; ctx.font = "14px sans-serif";
    ctx.fillText("请选择站点并导入光变曲线（或点击“示例曲线”）", 30, 40);
    return;
  }
  const s = cur.series;
  if (!lcState.view) lcResetView();
  const { t0, t1 } = lcState.view;

  // y 范围
  let ys = [];
  for (let i = 0; i < s.t.length; i++) {
    if (s.t[i] >= t0 && s.t[i] <= t1) ys.push(lcY(i));
  }
  const f = lcState.fit;
  if (f && f.model_y) {
    if (lcState.showNorm) ys.push(...f.model_y);
    else ys.push(...f.model_y.map((v, k) => v * lcBaseAt(f.model_t[k])));
  }
  let y0 = ys.length ? Math.min(...ys) : 0;
  let y1 = ys.length ? Math.max(...ys) : 1;
  let mg = (y1 - y0) * 0.12;
  if (!mg) mg = Math.abs(y0) * 0.1 || 0.05;
  y0 -= mg; y1 += mg;
  const m = lcMapFor(cv, t0, t1, y0, y1);
  lcState._map = m;

  // 网格与刻度
  ctx.strokeStyle = "rgba(120,150,190,.18)";
  ctx.fillStyle = "rgba(150,175,205,.6)";
  ctx.font = "10px sans-serif";
  ctx.lineWidth = 1;
  for (let k = 0; k <= 6; k++) {
    const tt = t0 + (t1 - t0) * k / 6;
    const x = m.X(tt);
    ctx.beginPath(); ctx.moveTo(x, LC_T); ctx.lineTo(x, LC_T + m.ph); ctx.stroke();
    ctx.fillText(fmtTime(tt), x - 22, H - 8);
  }
  const yPrec = (y1 - y0) < 0.2 ? 4 : 2;
  for (let k = 0; k <= 4; k++) {
    const vv = y0 + (y1 - y0) * k / 4;
    const yy = m.Y(vv);
    ctx.beginPath(); ctx.moveTo(LC_L, yy); ctx.lineTo(LC_L + m.pw, yy); ctx.stroke();
    ctx.fillText(vv.toFixed(yPrec), 4, yy + 3);
  }

  // 基线区间
  for (const [key, label] of [["pre", "前基线"], ["post", "后基线"]]) {
    const iv = lcState.baselines[key];
    if (!iv) continue;
    const x1 = m.X(Math.max(iv[0], t0)), x2 = m.X(Math.min(iv[1], t1));
    if (x2 <= x1) continue;
    ctx.fillStyle = "rgba(92,214,138,.13)";
    ctx.fillRect(x1, LC_T, x2 - x1, m.ph);
    ctx.fillStyle = "rgba(92,214,138,.8)";
    ctx.fillText(label, x1 + 4, LC_T + 12);
  }

  // 归一化参考线 / 原始视图基线
  if (lcState.showNorm && lcState.norm) {
    ctx.strokeStyle = "rgba(230,240,255,.35)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(LC_L, m.Y(1)); ctx.lineTo(LC_L + m.pw, m.Y(1)); ctx.stroke();
    ctx.setLineDash([]);
  } else if (lcState.norm && lcState.norm.base) {
    ctx.strokeStyle = "rgba(92,214,138,.75)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < s.t.length; i++) {
      const x = m.X(s.t[i]), yy = m.Y(lcState.norm.base[i]);
      i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
    }
    ctx.stroke();
    ctx.lineWidth = 1;
  }

  // 数据点与误差棒
  for (let i = 0; i < s.t.length; i++) {
    if (s.t[i] < t0 || s.t[i] > t1) continue;
    const x = m.X(s.t[i]), yy = m.Y(lcY(i));
    if (lcState.masked.has(i)) {
      ctx.strokeStyle = "rgba(150,158,168,.8)";
      ctx.beginPath();
      ctx.moveTo(x - 3.5, yy - 3.5); ctx.lineTo(x + 3.5, yy + 3.5);
      ctx.moveTo(x + 3.5, yy - 3.5); ctx.lineTo(x - 3.5, yy + 3.5);
      ctx.stroke();
      continue;
    }
    const e = lcYerr(i);
    if (e > 0) {
      ctx.strokeStyle = "rgba(158,197,255,.4)";
      ctx.beginPath();
      ctx.moveTo(x, m.Y(lcY(i) - e)); ctx.lineTo(x, m.Y(lcY(i) + e));
      ctx.stroke();
    }
    ctx.fillStyle = "#9ec5ff";
    ctx.beginPath(); ctx.arc(x, yy, 2, 0, 7); ctx.fill();
  }

  // 模型曲线与 D/R 标记
  if (f && f.model_t) {
    ctx.strokeStyle = f.ok ? "#4dd0e1" : "#ff9c6b";
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let k = 0; k < f.model_t.length; k++) {
      const v = lcState.showNorm ? f.model_y[k] : f.model_y[k] * lcBaseAt(f.model_t[k]);
      const x = m.X(f.model_t[k]), yy = m.Y(v);
      k ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
    }
    ctx.stroke();
    ctx.lineWidth = 1;
    for (const [tt, lab] of [[f.D, "D"], [f.R, "R"]]) {
      if (tt == null) continue;
      const x = m.X(tt);
      ctx.strokeStyle = "#ffd166";
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(x, LC_T); ctx.lineTo(x, LC_T + m.ph); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#ffd166";
      ctx.font = "bold 11px sans-serif";
      ctx.fillText(`${lab} ${fmtTime2(tt)}`, x + 4, LC_T + (lab === "D" ? 26 : 40));
      ctx.font = "10px sans-serif";
    }
  }

  // 拖拽框
  const d = lcState.drag;
  if (d && d.cur && lcState.mode !== "pan") {
    const x0 = Math.min(d.x0, d.cur[0]), x1 = Math.max(d.x0, d.cur[0]);
    let yA = LC_T, yB = LC_T + m.ph;
    if (lcState.mode === "mask") {
      yA = Math.min(d.y0, d.cur[1]); yB = Math.max(d.y0, d.cur[1]);
    }
    ctx.strokeStyle = "rgba(255,209,102,.9)";
    ctx.fillStyle = "rgba(255,209,102,.12)";
    ctx.fillRect(x0, yA, x1 - x0, yB - yA);
    ctx.strokeRect(x0, yA, x1 - x0, yB - yA);
  }
}

function lcDrawResid() {
  const cv = $("lcResidCanvas");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.fillStyle = "#0d1b2a";
  ctx.fillRect(0, 0, W, H);
  const f = lcState.fit;
  if (!f || !f.resid || !lcState.curve) {
    ctx.fillStyle = "#8fb8e8"; ctx.font = "12px sans-serif";
    ctx.fillText("拟合后此处显示残差", 30, 30);
    return;
  }
  const { t0, t1 } = lcState.view;
  const mset = new Set(f.masked || []);
  let rmax = (f.rms || 0.01) * 4;
  for (let i = 0; i < f.t.length; i++) {
    if (mset.has(i)) continue;
    rmax = Math.max(rmax, Math.abs(f.resid[i]) * 1.15);
  }
  const m = lcMapFor(cv, t0, t1, -rmax, rmax);
  // 网格与零线
  ctx.strokeStyle = "rgba(120,150,190,.18)";
  ctx.fillStyle = "rgba(150,175,205,.6)";
  ctx.font = "10px sans-serif";
  for (let k = 0; k <= 6; k++) {
    const x = m.X(t0 + (t1 - t0) * k / 6);
    ctx.beginPath(); ctx.moveTo(x, LC_T); ctx.lineTo(x, LC_T + m.ph); ctx.stroke();
  }
  ctx.strokeStyle = "rgba(230,240,255,.5)";
  ctx.beginPath(); ctx.moveTo(LC_L, m.Y(0)); ctx.lineTo(LC_L + m.pw, m.Y(0)); ctx.stroke();
  if (f.rms) {
    ctx.strokeStyle = "rgba(255,209,102,.4)";
    ctx.setLineDash([4, 4]);
    for (const sgn of [-1, 1]) {
      ctx.beginPath(); ctx.moveTo(LC_L, m.Y(sgn * f.rms)); ctx.lineTo(LC_L + m.pw, m.Y(sgn * f.rms)); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.fillText(`rms ${f.rms.toFixed(4)}`, LC_L + 4, m.Y(f.rms) - 3);
  }
  for (let i = 0; i < f.t.length; i++) {
    if (f.t[i] < t0 || f.t[i] > t1) continue;
    const x = m.X(f.t[i]), yy = m.Y(f.resid[i]);
    if (mset.has(i)) {
      ctx.strokeStyle = "rgba(150,158,168,.7)";
      ctx.beginPath();
      ctx.moveTo(x - 3, yy - 3); ctx.lineTo(x + 3, yy + 3);
      ctx.moveTo(x + 3, yy - 3); ctx.lineTo(x - 3, yy + 3);
      ctx.stroke();
    } else {
      ctx.fillStyle = "#ffd166";
      ctx.beginPath(); ctx.arc(x, yy, 2, 0, 7); ctx.fill();
    }
  }
}

/* ---------------- 结果与版本 ---------------- */
function lcRenderResult() {
  const div = $("lcResult");
  const warn = $("lcWarn");
  warn.innerHTML = "";
  const f = lcState.fit;
  if (!f) {
    div.innerHTML = "<span class='hint'>框选基线区间后点击“拟合接触时刻”；屏蔽点和基线阶数可随时调整。</span>";
    return;
  }
  if (!f.ok) {
    div.innerHTML = "<span class='hint'>拟合失败，原因如下：</span>";
    (f.reasons || []).forEach(r => warn.innerHTML += `<div class="conflict">✗ ${r}</div>`);
    (f.warnings || []).forEach(w => warn.innerHTML += `<div class="conflict">⚠ ${w}</div>`);
    return;
  }
  const rows = [
    ["消失时刻 D", `${fmtTime2(f.D)} ± ${fmt(f.sig_D, 2)} s`],
    ["复现时刻 R", `${fmtTime2(f.R)} ± ${fmt(f.sig_R, 2)} s`],
    ["掩星时长", `${fmt(f.dur, 2)} ± ${fmt(f.sig_dur, 2)} s`],
    ["深度", fmt(f.depth, 3)],
    ["水平", fmt(f.level, 4)],
    ["χ²/自由度", `${fmt(f.chi2, 1)} / ${f.dof}（约化 ${fmt(f.chi2_red, 2)}）`],
    ["参与点/屏蔽", `${f.n_used} / ${f.n_masked}`],
    ["基线 RMS", fmt(f.baseline ? f.baseline.rms : null, 4)],
  ];
  div.innerHTML = rows.map(([k, v]) => `<span><b>${k}</b></span><span>${v}</span>`).join("");
  (f.warnings || []).forEach(w => warn.innerHTML += `<div class="conflict">⚠ ${w}</div>`);
}

const LC_STATUS = { draft: "草稿", confirmed: "已确认", superseded: "被替代" };

function lcRenderVersions() {
  const ul = $("lcVersions");
  ul.innerHTML = "";
  const cur = lcState.curve;
  if (!cur) return;
  for (const v of cur.versions) {
    const li = document.createElement("li");
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = lcState.cmp.has(v.id);
    chk.title = "选择用于比较";
    chk.onchange = () => {
      chk.checked ? lcState.cmp.add(v.id) : lcState.cmp.delete(v.id);
      lcRenderCompare();
    };
    const r = v.result || {};
    const txt = r.ok
      ? `D ${fmtTime2(r.D)}±${fmt(r.sig_D, 2)} R ${fmtTime2(r.R)}±${fmt(r.sig_R, 2)}`
      : "拟合失败";
    const mid = document.createElement("span");
    mid.style.flex = "1";
    mid.innerHTML = `#${v.id} ${v.label} <span class="badge">${LC_STATUS[v.status] || v.status}</span>` +
      `<br><span class="hint">${txt}</span>`;
    const mkBtn = (label, fn, title) => {
      const b = document.createElement("button");
      b.textContent = label;
      if (title) b.title = title;
      b.onclick = fn;
      return b;
    };
    const bView = mkBtn("看", () => lcViewVersion(v), "载入该版本的设置并复算");
    const bOk = mkBtn("确认", () => lcConfirmVersion(v), "把该版本时刻写入该站正观测");
    if (!r.ok) bOk.disabled = true;
    const bDel = mkBtn("删", async () => {
      const r2 = await api(`/api/lcversions/${v.id}`, "DELETE");
      lcState.curve.versions = r2.versions;
      lcRenderVersions(); lcRenderCompare();
    });
    li.append(chk, mid, bView, bOk, bDel);
    ul.appendChild(li);
  }
}

async function lcViewVersion(v) {
  const st = v.settings || {};
  const bl = st.baselines || {};
  lcState.baselines = { pre: bl.pre || null, post: bl.post || null };
  lcState.masked = new Set(st.masked || []);
  lcState.degree = st.degree != null ? st.degree : 1;
  $("lcDegree").value = String(lcState.degree);
  await lcFit(`版本 #${v.id}「${v.label}」的复算结果`);
}

async function lcConfirmVersion(v) {
  try {
    const r = await api(`/api/lcversions/${v.id}/confirm`, "POST");
    lcState.curve.versions = r.versions;
    lcRenderVersions(); lcRenderCompare();
    alert(`已写入该站正观测（${r.action === "created" ? "新建" : "更新"} #${r.observation_id}）：\n` +
      `D = ${fmtTime2(r.t1)} ± ${fmt(r.err1, 2)} s\nR = ${fmtTime2(r.t2)} ± ${fmt(r.err2, 2)} s`);
    if (window.lcAfterConfirm) await window.lcAfterConfirm();
  } catch (ex) {
    alert("未写入：" + ex.message);
  }
}

function lcRenderCompare() {
  const tb = $("lcCmpTable").querySelector("tbody");
  tb.innerHTML = "";
  const cur = lcState.curve;
  if (!cur) return;
  const vers = cur.versions.filter(v => lcState.cmp.has(v.id)).sort((a, b) => a.id - b.id);
  if (!vers.length) {
    tb.innerHTML = "<tr><td colspan='11' class='hint'>勾选版本列表中的两个版本以比较时刻差异</td></tr>";
    return;
  }
  const base = vers[0].result || {};
  for (const v of vers) {
    const r = v.result || {};
    const isBase = v.id === vers[0].id;
    const diff = (a, b) => (r.ok && base.ok && !isBase && a != null && b != null) ? fmt(a - b, 2) : "—";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>#${v.id} ${v.label}${isBase ? "（基准）" : ""}</td>
      <td>${r.ok ? fmtTime2(r.D) : "失败"}</td><td>${fmt(r.sig_D, 2)}</td>
      <td>${r.ok ? fmtTime2(r.R) : "失败"}</td><td>${fmt(r.sig_R, 2)}</td>
      <td>${fmt(r.dur, 2)}</td><td>${fmt(r.depth, 3)}</td><td>${fmt(r.chi2_red, 2)}</td>
      <td>${diff(r.D, base.D)}</td><td>${diff(r.R, base.R)}</td><td>${diff(r.dur, base.dur)}</td>`;
    tb.appendChild(tr);
  }
}

/* ---------------- 画布交互 ---------------- */
function lcCanvasPos(e) {
  const cv = $("lcCanvas");
  const rect = cv.getBoundingClientRect();
  return [(e.clientX - rect.left) * cv.width / rect.width,
          (e.clientY - rect.top) * cv.height / rect.height];
}

function lcToggleNearest(px, py) {
  const cur = lcState.curve, m = lcState._map;
  if (!cur || !m) return;
  const s = cur.series;
  let best = -1, bestD = 15;
  for (let i = 0; i < s.t.length; i++) {
    const dx = m.X(s.t[i]) - px, dy = m.Y(lcY(i)) - py;
    const d = Math.hypot(dx, dy);
    if (d < bestD) { bestD = d; best = i; }
  }
  if (best >= 0) {
    lcState.masked.has(best) ? lcState.masked.delete(best) : lcState.masked.add(best);
    lcSettingsChanged();
  }
}

function lcBindCanvas() {
  const cv = $("lcCanvas");
  cv.addEventListener("wheel", (e) => {
    if (!lcState.curve || !lcState._map) return;
    e.preventDefault();
    const [px] = lcCanvasPos(e);
    const tc = lcState._map.T(px);
    const fac = e.deltaY > 0 ? 1.25 : 0.8;
    const { t0, t1 } = lcState.view;
    const nt0 = tc - (tc - t0) * fac, nt1 = tc + (t1 - tc) * fac;
    if (nt1 - nt0 < 0.02) return;
    lcState.view = { t0: nt0, t1: nt1 };
    lcDraw(); lcDrawResid();
  }, { passive: false });

  cv.addEventListener("mousedown", (e) => {
    if (!lcState.curve) return;
    const [px, py] = lcCanvasPos(e);
    lcState.drag = { x0: px, y0: py, cur: null, moved: false, startView: { ...lcState.view } };
  });

  window.addEventListener("mousemove", (e) => {
    const d = lcState.drag;
    if (!d) return;
    const [px, py] = lcCanvasPos(e);
    if (Math.abs(px - d.x0) + Math.abs(py - d.y0) > 4) d.moved = true;
    if (lcState.mode === "pan") {
      const m = lcState._map;
      const dt = (d.x0 - px) / m.pw * (d.startView.t1 - d.startView.t0);
      lcState.view = { t0: d.startView.t0 + dt, t1: d.startView.t1 + dt };
    } else if (d.moved) {
      d.cur = [px, py];
    }
    lcDraw(); lcDrawResid();
  });

  window.addEventListener("mouseup", (e) => {
    const d = lcState.drag;
    if (!d) return;
    lcState.drag = null;
    const m = lcState._map;
    if (lcState.mode === "pan") { lcDraw(); lcDrawResid(); return; }
    if (!d.moved) {
      if (lcState.mode === "mask") {
        const [px, py] = lcCanvasPos(e);
        lcToggleNearest(px, py);
      }
      return;
    }
    if (!d.cur || !m) return;
    const ta = m.T(Math.min(d.x0, d.cur[0]));
    const tb = m.T(Math.max(d.x0, d.cur[0]));
    if (lcState.mode === "pre") lcState.baselines.pre = [ta, tb];
    else if (lcState.mode === "post") lcState.baselines.post = [ta, tb];
    else if (lcState.mode === "mask") {
      const va = m.V(Math.max(d.y0, d.cur[1]));
      const vb = m.V(Math.min(d.y0, d.cur[1]));
      const s = lcState.curve.series;
      for (let i = 0; i < s.t.length; i++) {
        const v = lcY(i);
        if (s.t[i] >= ta && s.t[i] <= tb && v >= va && v <= vb) lcState.masked.add(i);
      }
    }
    lcSettingsChanged();
  });

  cv.addEventListener("dblclick", () => {
    if (!lcState.curve) return;
    lcResetView();
    lcDraw(); lcDrawResid();
  });
}

/* ---------------- 绑定 ---------------- */
function lcBind() {
  document.querySelectorAll(".lcMode").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".lcMode").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      lcState.mode = btn.dataset.lcmode;
    };
  });
  $("btnCloseLc").onclick = () => $("lcModal").classList.add("hidden");
  $("lcStation").onchange = () => lcLoadCurves();
  $("lcCurve").onchange = (e) => lcSelectCurve(+e.target.value);
  $("lcDegree").onchange = (e) => {
    lcState.degree = +e.target.value;
    lcSettingsChanged();
  };
  $("lcShowNorm").onchange = (e) => {
    lcState.showNorm = e.target.checked;
    if (lcState.showNorm && !lcState.norm) lcPreview();
    else { lcDraw(); lcDrawResid(); }
  };
  $("btnLcClearMask").onclick = () => {
    lcState.masked.clear();
    lcSettingsChanged();
  };
  $("btnLcFit").onclick = () => lcFit();
  $("btnLcSaveVer").onclick = async () => {
    if (!lcState.curve) return;
    if (!lcState.baselines.pre && !lcState.baselines.post)
      return alert("请先框选基线区间");
    const label = (prompt("版本说明", "") || "").trim();
    const r = await api(`/api/lightcurves/${lcState.curve.id}/versions`, "POST",
      { label: label || undefined, ...lcSettings() });
    lcState.curve.versions = r.versions;
    lcRenderVersions(); lcRenderCompare();
  };
  $("btnLcImport").onclick = async () => {
    const sid = +$("lcStation").value;
    if (!sid) return alert("请先选择站点");
    const text = $("lcCsv").value;
    if (!text.trim()) return;
    try {
      const r = await api(`/api/stations/${sid}/lightcurves`, "POST",
        { name: $("lcName").value.trim(), text });
      $("lcCsv").value = $("lcName").value = "";
      $("lcImportMsg").textContent =
        `已导入 ${r.n_points} 点` +
        (r.import_errors && r.import_errors.length ? "；跳过：" + r.import_errors.join("；") : "");
      await lcLoadCurves(r.id);
    } catch (ex) {
      $("lcImportMsg").textContent = "导入失败：" + ex.message;
    }
  };
  $("btnLcDemo").onclick = async () => {
    const sid = +$("lcStation").value;
    if (!sid) return alert("请先选择站点");
    const r = await api(`/api/stations/${sid}/lightcurves/demo`, "POST");
    $("lcImportMsg").textContent =
      `已生成示例曲线（真实 D=${fmtTime2(r.truth.D)} R=${fmtTime2(r.truth.R)}，含 4 个离群点）`;
    await lcLoadCurves(r.id);
  };
  $("btnLcDelCurve").onclick = async () => {
    if (!lcState.curve) return;
    if (!confirm(`删除曲线 #${lcState.curve.id}「${lcState.curve.name}」及其全部判读版本？`)) return;
    await api(`/api/lightcurves/${lcState.curve.id}`, "DELETE");
    await lcLoadCurves();
  };
  lcBindCanvas();
}

lcBind();
