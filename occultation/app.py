"""掩星弦线拟合台 —— Flask + SQLite 后端。"""
import json
import os
import re
import sqlite3
import time

from flask import Flask, g, jsonify, request, send_from_directory

import fitter
import lightcurve

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
  err2 REAL DEFAULT 0.0,
  origin TEXT NOT NULL DEFAULT 'manual',  -- manual | lightcurve（判读写入）
  lc_version_id INTEGER            -- 产生该观测的判读版本
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  created_at REAL,
  payload TEXT NOT NULL            -- JSON：事件参数+站点+观测+拟合结果
);
CREATE TABLE IF NOT EXISTS lightcurves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '',
  created_at REAL,
  csv_text TEXT NOT NULL DEFAULT '',  -- 原始 CSV 文本
  n_points INTEGER NOT NULL DEFAULT 0,
  t_start REAL, t_end REAL,
  series TEXT NOT NULL                -- JSON：t/flux/err/exp 原始序列
);
CREATE TABLE IF NOT EXISTS lc_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lightcurve_id INTEGER NOT NULL REFERENCES lightcurves(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',  -- draft | confirmed | superseded
  created_at REAL,
  confirmed_at REAL,
  settings TEXT NOT NULL,           -- JSON：基线区间/屏蔽点/基线阶数
  result TEXT NOT NULL              -- JSON：拟合结果（标量、基线与说明）
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
    # 迁移：observations 增加判读来源标记（旧库无此列）
    cols = [r[1] for r in db.execute("PRAGMA table_info(observations)")]
    if "origin" not in cols:
        db.execute("ALTER TABLE observations "
                   "ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'")
    if "lc_version_id" not in cols:
        db.execute("ALTER TABLE observations ADD COLUMN lc_version_id INTEGER")
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


@app.put("/api/observations/<int:oid>")
def update_observation(oid):
    """手工修改观测时刻/误差。修改后该观测标记为 manual，
    后续判读版本确认时不会再覆盖它。"""
    d = request.get_json(force=True)
    o = row("SELECT * FROM observations WHERE id=?", (oid,))
    if not o:
        return err("观测不存在", 404)
    fields, args = [], []
    for k in ("t1", "t2", "err1", "err2"):
        if k in d:
            fields.append(f"{k}=?")
            args.append(None if d[k] is None else float(d[k]))
    if not fields:
        return err("没有可更新的字段")
    merged = dict(o)
    merged.update({k: v for k, v in d.items() if k in ("t1", "t2")})
    if o["kind"] == "positive" and merged.get("t1") is not None \
            and merged.get("t2") is not None and merged["t2"] <= merged["t1"]:
        return err("复现时刻应晚于消失时刻")
    fields.append("origin='manual'")   # 手工修改后脱离判读自动维护
    args.append(oid)
    get_db().execute(f"UPDATE observations SET {','.join(fields)} WHERE id=?", args)
    get_db().commit()
    return jsonify(row("SELECT * FROM observations WHERE id=?", (oid,)))


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


# ---------------------------------------------------------------- 光变曲线判读

def _lc_versions(lcid):
    vers = rows("SELECT * FROM lc_versions WHERE lightcurve_id=? "
                "ORDER BY id DESC", (lcid,))
    for v in vers:
        v["settings"] = json.loads(v["settings"])
        v["result"] = json.loads(v["result"])
    return vers


def _lc_state(lcid):
    lc = row("SELECT * FROM lightcurves WHERE id=?", (lcid,))
    if not lc:
        return None
    lc.pop("csv_text", None)           # 原始 CSV 留存库中，不必每次下发
    lc["series"] = json.loads(lc["series"])
    lc["versions"] = _lc_versions(lcid)
    return lc


def _insert_lightcurve(sid, name, csv_text, series):
    cur = get_db().execute(
        "INSERT INTO lightcurves(station_id,name,created_at,csv_text,"
        "n_points,t_start,t_end,series) VALUES(?,?,?,?,?,?,?,?)",
        (sid, name, time.time(), csv_text, len(series["t"]),
         series["t"][0], series["t"][-1],
         json.dumps({k: series[k] for k in ("t", "flux", "err", "exp")})))
    get_db().commit()
    return cur.lastrowid


@app.get("/api/stations/<int:sid>/lightcurves")
def list_lightcurves(sid):
    return jsonify(rows(
        "SELECT id,station_id,name,created_at,n_points,t_start,t_end "
        "FROM lightcurves WHERE station_id=? ORDER BY id DESC", (sid,)))


@app.post("/api/stations/<int:sid>/lightcurves")
def import_lightcurve(sid):
    """导入 “时间,流量,误差,曝光时长” CSV（时间支持 HH:MM:SS.s 或秒）。"""
    if not row("SELECT * FROM stations WHERE id=?", (sid,)):
        return err("站点不存在", 404)
    d = request.get_json(force=True) or {}
    try:
        series, errors = lightcurve.parse_csv(d.get("text", ""))
    except lightcurve.LcError as ex:
        return err(str(ex))
    name = d.get("name") or "光变曲线 %s" % time.strftime("%H:%M:%S")
    lcid = _insert_lightcurve(sid, name, d.get("text", ""), series)
    state = _lc_state(lcid)
    state["import_errors"] = errors
    return jsonify(state), 201


@app.post("/api/stations/<int:sid>/lightcurves/demo")
def demo_lightcurve(sid):
    """生成一条示例光变曲线（含噪声与离群点），用于练习判读流程。"""
    if not row("SELECT * FROM stations WHERE id=?", (sid,)):
        return err("站点不存在", 404)
    s = lightcurve.demo_series(seed=20260914 + sid)
    lines = ["# time, flux, err, exp"]
    for i in range(len(s["t"])):
        lines.append("%.2f, %.5f, %.4f, %.2f"
                     % (s["t"][i], s["flux"][i], s["err"][i], s["exp"][i]))
    lcid = _insert_lightcurve(sid, "示例曲线", "\n".join(lines), s)
    state = _lc_state(lcid)
    state["truth"] = s["truth"]
    return jsonify(state), 201


@app.get("/api/lightcurves/<int:lcid>")
def get_lightcurve(lcid):
    lc = _lc_state(lcid)
    return jsonify(lc) if lc else err("光变曲线不存在", 404)


@app.delete("/api/lightcurves/<int:lcid>")
def delete_lightcurve(lcid):
    get_db().execute("DELETE FROM lightcurves WHERE id=?", (lcid,))
    get_db().commit()
    return jsonify({"ok": True})


def _lc_fit_input(lcid):
    lc = row("SELECT * FROM lightcurves WHERE id=?", (lcid,))
    if not lc:
        return None, None, None
    d = request.get_json(force=True) or {}
    settings = {
        "baselines": d.get("baselines") or {},
        "masked": [int(i) for i in (d.get("masked") or [])],
        "degree": 1 if int(d.get("degree", 1)) else 0,
    }
    return lc, json.loads(lc["series"]), settings


@app.post("/api/lightcurves/<int:lcid>/preview")
def lc_preview(lcid):
    """归一化预览：返回归一化序列与基线（不落库）。"""
    lc, series, settings = _lc_fit_input(lcid)
    if not lc:
        return err("光变曲线不存在", 404)
    try:
        norm = lightcurve.normalize(series, settings["baselines"],
                                    settings["masked"], settings["degree"])
    except lightcurve.LcError as ex:
        return err(str(ex))
    return jsonify({"norm": norm})


@app.post("/api/lightcurves/<int:lcid>/fit")
def lc_fit(lcid):
    """双阶跃拟合（不落库）。返回模型曲线、残差、不确定度与失败原因。"""
    lc, series, settings = _lc_fit_input(lcid)
    if not lc:
        return err("光变曲线不存在", 404)
    result = lightcurve.fit_curve(series, settings["baselines"],
                                  settings["masked"], settings["degree"])
    return jsonify(result)


@app.post("/api/lightcurves/<int:lcid>/versions")
def save_lc_version(lcid):
    """把当前判读（基线区间+屏蔽点+拟合设置）保存为版本；
    服务端按设置重算拟合，保证版本内容可复查。"""
    lc, series, settings = _lc_fit_input(lcid)
    if not lc:
        return err("光变曲线不存在", 404)
    d = request.get_json(force=True) or {}
    result = lightcurve.fit_curve(series, settings["baselines"],
                                  settings["masked"], settings["degree"])
    label = d.get("label") or "判读 %s" % time.strftime("%H:%M:%S")
    cur = get_db().execute(
        "INSERT INTO lc_versions(lightcurve_id,label,status,created_at,"
        "settings,result) VALUES(?,?,'draft',?,?,?)",
        (lcid, label, time.time(),
         json.dumps(settings, ensure_ascii=False),
         json.dumps(lightcurve.trim_result(result), ensure_ascii=False)))
    get_db().commit()
    return jsonify({"id": cur.lastrowid, "label": label,
                    "versions": _lc_versions(lcid)}), 201


@app.get("/api/lcversions/<int:vid>")
def get_lc_version(vid):
    v = row("SELECT * FROM lc_versions WHERE id=?", (vid,))
    if not v:
        return err("版本不存在", 404)
    v["settings"] = json.loads(v["settings"])
    v["result"] = json.loads(v["result"])
    return jsonify(v)


@app.delete("/api/lcversions/<int:vid>")
def delete_lc_version(vid):
    v = row("SELECT lightcurve_id FROM lc_versions WHERE id=?", (vid,))
    if not v:
        return err("版本不存在", 404)
    get_db().execute("DELETE FROM lc_versions WHERE id=?", (vid,))
    get_db().commit()
    return jsonify({"versions": _lc_versions(v["lightcurve_id"])})


@app.post("/api/lcversions/<int:vid>/confirm")
def confirm_lc_version(vid):
    """确认版本：把拟合时刻写入该站正观测。

    保护规则：站点若存在手工填写/修改过的正观测（origin='manual'），
    拒绝覆盖并说明；只更新此前由判读写入的观测。
    """
    v = row("SELECT * FROM lc_versions WHERE id=?", (vid,))
    if not v:
        return err("版本不存在", 404)
    result = json.loads(v["result"])
    if not result.get("ok"):
        return err("该版本拟合未成功，没有可写入的接触时刻：%s"
                   % "；".join(result.get("reasons") or ["未知原因"]))
    lc = row("SELECT * FROM lightcurves WHERE id=?", (v["lightcurve_id"],))
    sid = lc["station_id"]
    positives = rows("SELECT * FROM observations WHERE station_id=? "
                     "AND kind='positive' ORDER BY id", (sid,))
    manual = [o for o in positives if o.get("origin", "manual") == "manual"]
    if manual:
        return jsonify({
            "ok": False, "protected": True,
            "error": "该站已有手工填写/修改的正观测时刻（%d 条），"
                     "为避免覆盖手工数据，本次未写入。请先删除对应观测记录，"
                     "再重新确认版本。" % len(manual),
            "manual_obs": manual,
        }), 409
    D, R = float(result["D"]), float(result["R"])
    sD = float(result.get("sig_D") or 0.0)
    sR = float(result.get("sig_R") or 0.0)
    db = get_db()
    own = [o for o in positives if o.get("origin") == "lightcurve"]
    if own:
        oid = own[0]["id"]
        db.execute("UPDATE observations SET t1=?,t2=?,err1=?,err2=?,"
                   "lc_version_id=? WHERE id=?", (D, R, sD, sR, vid, oid))
        action = "updated"
    else:
        cur = db.execute(
            "INSERT INTO observations(station_id,kind,t1,t2,err1,err2,"
            "origin,lc_version_id) VALUES(?,'positive',?,?,?,?,'lightcurve',?)",
            (sid, D, R, sD, sR, vid))
        oid = cur.lastrowid
        action = "created"
    db.execute("UPDATE lc_versions SET status='superseded' "
               "WHERE lightcurve_id=? AND status='confirmed' AND id<>?",
               (v["lightcurve_id"], vid))
    db.execute("UPDATE lc_versions SET status='confirmed', confirmed_at=? "
               "WHERE id=?", (time.time(), vid))
    db.commit()
    return jsonify({"ok": True, "action": action, "observation_id": oid,
                    "t1": D, "t2": R, "err1": sD, "err2": sR,
                    "versions": _lc_versions(v["lightcurve_id"])})


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
