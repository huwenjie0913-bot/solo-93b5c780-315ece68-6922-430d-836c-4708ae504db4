"""掩星弦线拟合台 —— Flask + SQLite 后端。"""
import json
import os
import re
import sqlite3
import time

from flask import Flask, g, jsonify, request, send_from_directory

import fitter

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "occultation.db")

app = Flask(__name__, static_folder=os.path.join(BASE, "static"),
            static_url_path="")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  star TEXT DEFAULT '',
  date TEXT DEFAULT '',
  velocity REAL NOT NULL DEFAULT 20.0,   -- 影子速度 km/s
  direction REAL NOT NULL DEFAULT 90.0,  -- 运动方向 PA，自北向东，度
  time_offset REAL NOT NULL DEFAULT 0.0, -- 统一时间偏移 s
  model TEXT NOT NULL DEFAULT 'circle',  -- circle | ellipse
  notes TEXT DEFAULT '',
  created_at REAL
);
CREATE TABLE IF NOT EXISTS stations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  excluded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,              -- positive | negative
  t1 REAL,                         -- 正:消失时刻 负:观测时刻（秒）
  t2 REAL,                         -- 正:复现时刻
  err1 REAL DEFAULT 0.0,           -- 对应计时误差 s
  err2 REAL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  created_at REAL,
  payload TEXT NOT NULL            -- JSON：事件参数+站点+观测+拟合结果
);
"""


# ---------------------------------------------------------------- 基础

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.commit()
    db.close()


def rows(sql, args=()):
    return [dict(r) for r in get_db().execute(sql, args).fetchall()]


def row(sql, args=()):
    r = get_db().execute(sql, args).fetchone()
    return dict(r) if r else None


def err(msg, code=400):
    return jsonify({"error": msg}), code


def event_state(event_id):
    ev = row("SELECT * FROM events WHERE id=?", (event_id,))
    if not ev:
        return None
    ev["stations"] = rows(
        "SELECT * FROM stations WHERE event_id=? ORDER BY id", (event_id,))
    obs = rows(
        "SELECT o.*, s.name AS station_name FROM observations o "
        "JOIN stations s ON s.id=o.station_id "
        "WHERE s.event_id=? ORDER BY o.id", (event_id,))
    ev["observations"] = obs
    ev["snapshots"] = rows(
        "SELECT id,label,created_at FROM snapshots WHERE event_id=? "
        "ORDER BY id DESC", (event_id,))
    return ev


def current_fit(event_id, model=None, time_offset=None):
    ev = event_state(event_id)
    if not ev:
        return None, None
    model = model or ev["model"]
    time_offset = ev["time_offset"] if time_offset is None else time_offset
    if not ev["stations"]:
        return ev, {"ok": False, "warnings": ["尚无站点"], "chords": [],
                    "negatives": [], "residuals": [], "conflicts": [],
                    "fit": None, "model": model, "time_offset": time_offset}
    return ev, fitter.run_fit(ev, ev["stations"], ev["observations"],
                              model, time_offset)


# ---------------------------------------------------------------- 页面

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------- 事件

@app.get("/api/events")
def list_events():
    return jsonify(rows("SELECT * FROM events ORDER BY id DESC"))


@app.post("/api/events")
def create_event():
    d = request.get_json(force=True)
    if not d.get("name"):
        return err("缺少事件名称")
    cur = get_db().execute(
        "INSERT INTO events(name,star,date,velocity,direction,notes,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (d["name"], d.get("star", ""), d.get("date", ""),
         float(d.get("velocity", 20.0)), float(d.get("direction", 90.0)),
         d.get("notes", ""), time.time()))
    get_db().commit()
    return jsonify(event_state(cur.lastrowid)), 201


@app.get("/api/events/<int:eid>")
def get_event(eid):
    ev = event_state(eid)
    return jsonify(ev) if ev else err("事件不存在", 404)


@app.put("/api/events/<int:eid>")
def update_event(eid):
    d = request.get_json(force=True)
    ev = row("SELECT * FROM events WHERE id=?", (eid,))
    if not ev:
        return err("事件不存在", 404)
    fields, args = [], []
    for k in ("name", "star", "date", "velocity", "direction",
              "time_offset", "model", "notes"):
        if k in d:
            fields.append(f"{k}=?")
            args.append(d[k])
    if fields:
        args.append(eid)
        get_db().execute(f"UPDATE events SET {','.join(fields)} WHERE id=?", args)
        get_db().commit()
    return jsonify(event_state(eid))


@app.delete("/api/events/<int:eid>")
def delete_event(eid):
    get_db().execute("DELETE FROM events WHERE id=?", (eid,))
    get_db().commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 站点

@app.post("/api/events/<int:eid>/stations")
def add_station(eid):
    d = request.get_json(force=True)
    if not d.get("name") or d.get("lat") is None or d.get("lon") is None:
        return err("站点需要 name/lat/lon")
    cur = get_db().execute(
        "INSERT INTO stations(event_id,name,lat,lon) VALUES(?,?,?,?)",
        (eid, d["name"], float(d["lat"]), float(d["lon"])))
    get_db().commit()
    return jsonify(row("SELECT * FROM stations WHERE id=?", (cur.lastrowid,))), 201


@app.put("/api/stations/<int:sid>")
def update_station(sid):
    d = request.get_json(force=True)
    fields, args = [], []
    for k in ("name", "lat", "lon", "excluded"):
        if k in d:
            fields.append(f"{k}=?")
            args.append(int(d[k]) if k == "excluded" else d[k])
    if fields:
        args.append(sid)
        get_db().execute(f"UPDATE stations SET {','.join(fields)} WHERE id=?", args)
        get_db().commit()
    return jsonify(row("SELECT * FROM stations WHERE id=?", (sid,)))


@app.delete("/api/stations/<int:sid>")
def delete_station(sid):
    get_db().execute("DELETE FROM stations WHERE id=?", (sid,))
    get_db().commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 观测

@app.post("/api/stations/<int:sid>/observations")
def add_observation(sid):
    d = request.get_json(force=True)
    kind = d.get("kind")
    if kind not in ("positive", "negative"):
        return err("kind 必须是 positive 或 negative")
    if kind == "positive" and (d.get("t1") is None or d.get("t2") is None):
        return err("正观测需要消失与复现时刻")
    if kind == "negative" and d.get("t1") is None:
        return err("负观测需要观测时刻")
    cur = get_db().execute(
        "INSERT INTO observations(station_id,kind,t1,t2,err1,err2)"
        " VALUES(?,?,?,?,?,?)",
        (sid, kind, d.get("t1"), d.get("t2"),
         float(d.get("err1") or 0), float(d.get("err2") or 0)))
    get_db().commit()
    return jsonify(row("SELECT * FROM observations WHERE id=?",
                       (cur.lastrowid,))), 201


@app.delete("/api/observations/<int:oid>")
def delete_observation(oid):
    get_db().execute("DELETE FROM observations WHERE id=?", (oid,))
    get_db().commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- CSV 导入

TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)\s*$")


def parse_time(s):
    m = TIME_RE.match(str(s))
    if not m:
        raise ValueError("时间格式应为 HH:MM:SS[.s]，得到 %r" % s)
    h, mnt, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + sec


@app.post("/api/events/<int:eid>/import")
def import_csv(eid):
    """逐行导入：
    STA, 站名, 纬度, 经度
    POS, 站名, 消失HH:MM:SS.s, 复现HH:MM:SS.s, 消失误差s, 复现误差s
    NEG, 站名, 观测HH:MM:SS.s, 误差s
    """
    text = (request.get_json(force=True) or {}).get("text", "")
    db = get_db()
    sta_id = {s["name"]: s["id"] for s in
              rows("SELECT * FROM stations WHERE event_id=?", (eid,))}
    added = {"stations": 0, "positive": 0, "negative": 0}
    errors = []

    def ensure_station(name, lat=None, lon=None):
        if name in sta_id:
            return sta_id[name]
        if lat is None:
            raise ValueError("站点 %s 未定义且行内无坐标" % name)
        cur = db.execute(
            "INSERT INTO stations(event_id,name,lat,lon) VALUES(?,?,?,?)",
            (eid, name, float(lat), float(lon)))
        sta_id[name] = cur.lastrowid
        added["stations"] += 1
        return cur.lastrowid

    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            tag = parts[0].upper()
            if tag in ("STA", "STATION"):
                ensure_station(parts[1], parts[2], parts[3])
            elif tag == "POS":
                sid = ensure_station(parts[1])
                db.execute(
                    "INSERT INTO observations(station_id,kind,t1,t2,err1,err2)"
                    " VALUES(?,?,?,?,?,?)",
                    (sid, "positive", parse_time(parts[2]), parse_time(parts[3]),
                     float(parts[4] or 0), float(parts[5] or 0)))
                added["positive"] += 1
            elif tag == "NEG":
                sid = ensure_station(parts[1])
                db.execute(
                    "INSERT INTO observations(station_id,kind,t1,t2,err1,err2)"
                    " VALUES(?,'negative',?,NULL,?,0)",
                    (sid, parse_time(parts[2]), float(parts[3] or 0)))
                added["negative"] += 1
            else:
                raise ValueError("未知行类型 %r" % parts[0])
        except (ValueError, IndexError) as ex:
            errors.append("第 %d 行：%s" % (ln, ex))
    db.commit()
    return jsonify({"added": added, "errors": errors})


# ---------------------------------------------------------------- 拟合

@app.post("/api/events/<int:eid>/fit")
def fit(eid):
    d = request.get_json(force=True) or {}
    model = d.get("model")
    offset = d.get("time_offset")
    # 同步保存当前拟合设置，保证快照一致
    sets, args = [], []
    if model in ("circle", "ellipse"):
        sets.append("model=?"); args.append(model)
    if offset is not None:
        sets.append("time_offset=?"); args.append(float(offset))
    if sets:
        args.append(eid)
        get_db().execute(f"UPDATE events SET {','.join(sets)} WHERE id=?", args)
        get_db().commit()
    ev, result = current_fit(eid, model, offset)
    if ev is None:
        return err("事件不存在", 404)
    return jsonify(result)


# ---------------------------------------------------------------- 快照

@app.post("/api/events/<int:eid>/snapshots")
def save_snapshot(eid):
    d = request.get_json(force=True) or {}
    ev, result = current_fit(eid, d.get("model"), d.get("time_offset"))
    if ev is None:
        return err("事件不存在", 404)
    payload = {
        "label": d.get("label") or "方案 %s" % time.strftime("%H:%M:%S"),
        "event": {k: ev[k] for k in
                  ("name", "star", "date", "velocity", "direction",
                   "time_offset", "model", "notes")},
        "stations": ev["stations"],
        "observations": ev["observations"],
        "fit_result": result,
    }
    cur = get_db().execute(
        "INSERT INTO snapshots(event_id,label,created_at,payload) VALUES(?,?,?,?)",
        (eid, payload["label"], time.time(), json.dumps(payload, ensure_ascii=False)))
    get_db().commit()
    return jsonify({"id": cur.lastrowid, "label": payload["label"]}), 201


@app.get("/api/snapshots/<int:sid>")
def get_snapshot(sid):
    s = row("SELECT * FROM snapshots WHERE id=?", (sid,))
    if not s:
        return err("快照不存在", 404)
    s["payload"] = json.loads(s["payload"])
    return jsonify(s)


@app.delete("/api/snapshots/<int:sid>")
def delete_snapshot(sid):
    get_db().execute("DELETE FROM snapshots WHERE id=?", (sid,))
    get_db().commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 示例数据

@app.post("/api/events/<int:eid>/demo")
def load_demo(eid):
    """生成一组自洽的示例观测：真实轮廓为椭圆，正反观测由它推出。"""
    import random
    import numpy as np
    import fitter as F

    ev = row("SELECT * FROM events WHERE id=?", (eid,))
    if not ev:
        return err("事件不存在", 404)
    rng = random.Random(20260914)
    v, pa = 18.5, 70.0
    a_true, b_true, phi_true = 85.0, 60.0, 25.0
    C0 = np.array([12.0, -8.0])          # t_ref 时刻轮廓中心（局部坐标）
    t_ref = 3 * 3600 + 14 * 60 + 25.0
    th = np.radians(pa)
    d = np.array([np.sin(th), np.cos(th)])
    A, B = F._ellipse_frame(phi_true)

    db = get_db()
    db.execute("DELETE FROM stations WHERE event_id=?", (eid,))
    lat0, lon0 = 31.0, 121.0
    offsets = [-130, -102, -78, -51, -30, -8, 14, 36, 58, 81, 104, 128]
    n_pos = n_neg = 0
    for i, off in enumerate(offsets):
        lat = lat0 + off / F.KM_PER_DEG
        lon = lon0 + rng.uniform(-0.06, 0.06)
        name = "站%02d" % (i + 1)
        cur = db.execute(
            "INSERT INTO stations(event_id,name,lat,lon) VALUES(?,?,?,?)",
            (eid, name, round(lat, 5), round(lon, 5)))
        p = np.array([(lon - lon0) * np.cos(np.radians(lat0)) * F.KM_PER_DEG,
                      off])
        r0 = p - C0
        # r(τ) = r0 - v τ d，在椭圆系内求交
        rA, rB = float(r0 @ A), float(r0 @ B)
        dA, dB = float(d @ A), float(d @ B)
        qa = (v * dA / a_true) ** 2 + (v * dB / b_true) ** 2
        qb = -2 * v * (rA * dA / a_true ** 2 + rB * dB / b_true ** 2)
        qc = (rA / a_true) ** 2 + (rB / b_true) ** 2 - 1
        disc = qb * qb - 4 * qa * qc
        if disc > 0:
            t1 = t_ref + (-qb - np.sqrt(disc)) / (2 * qa) + rng.gauss(0, 0.25)
            t2 = t_ref + (-qb + np.sqrt(disc)) / (2 * qa) + rng.gauss(0, 0.25)
            e1 = round(rng.uniform(0.15, 0.4), 2)
            e2 = round(rng.uniform(0.15, 0.4), 2)
            db.execute(
                "INSERT INTO observations(station_id,kind,t1,t2,err1,err2)"
                " VALUES(?,'positive',?,?,?,?)",
                (cur.lastrowid, round(t1, 2), round(t2, 2), e1, e2))
            n_pos += 1
        else:
            t = t_ref - qb / (2 * qa)
            db.execute(
                "INSERT INTO observations(station_id,kind,t1,t2,err1,err2)"
                " VALUES(?,'negative',?,NULL,0.3,0)", (cur.lastrowid, round(t, 2)))
            n_neg += 1
    db.execute("UPDATE events SET velocity=?, direction=?, time_offset=0,"
               " model='ellipse' WHERE id=?", (v, pa, eid))
    db.commit()
    return jsonify({"ok": True, "positive": n_pos, "negative": n_neg,
                    "truth": {"a": a_true, "b": b_true, "phi": phi_true,
                              "cx": C0[0], "cy": C0[1]}})


# ---------------------------------------------------------------- 导出

@app.get("/api/events/<int:eid>/export")
def export(eid):
    ev, result = current_fit(eid)
    if ev is None:
        return err("事件不存在", 404)
    anomalies = list(result.get("conflicts", []))
    for r in result.get("residuals", []):
        if r.get("flag"):
            anomalies.append("站点 %s：%s" % (r["station"], r["flag"]))
    anomalies.extend(result.get("warnings", []))
    doc = {
        "description": "掩星弦线拟合导出：原始输入 + 处理结果 + 异常说明",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": {k: ev[k] for k in
                  ("name", "star", "date", "velocity", "direction",
                   "time_offset", "model", "notes")},
        "inputs": {
            "stations": ev["stations"],
            "observations": ev["observations"],
        },
        "processed": result,
        "anomalies": anomalies,
    }
    return app.response_class(
        json.dumps(doc, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition":
                 "attachment; filename=occultation_event_%d.json" % eid})


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
