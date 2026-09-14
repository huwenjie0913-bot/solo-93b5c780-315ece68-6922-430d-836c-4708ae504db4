"""掩星弦线换算与加权最小二乘轮廓拟合。

坐标约定（局部切平面 / 基本平面）：
  x 轴指向东，y 轴指向北，单位 km，原点取全部站点的平均经纬度。
  影子运动方向 pa（position angle，度）自北向东量取，
  运动方向单位矢量 d = (sin pa, cos pa)，弦线沿 d 方向（站点相对影子
  中心沿 -d 扫过），弦线法向 n = (cos pa, -sin pa)。

正观测：消失时刻 t1、复现时刻 t2（秒，任意共同零点）。
  中点时刻 t0 = (t1+t2)/2 + 全局时间偏移；
  弦线中点 q = p - v*(t0 - t_ref) * d   （t_ref 取全部中点时刻均值）
  半弦长 L = v * (t2 - t1)/2
  计时误差 -> 中点沿迹不确定 v*sqrt(e1^2+e2^2)/2，半弦长不确定 v*sqrt(e1^2+e2^2)/2

负观测：t 时刻星未被掩 -> 点 p - v*(t+offset-t_ref)*d 必须在轮廓之外。
"""
import math

import numpy as np

KM_PER_DEG = 111.195  # 平均每度公里数（局部近似足够）


# ---------------------------------------------------------------- 坐标与弦线

def station_xy(stations):
    """站点经纬度 -> 局部平面坐标。返回 (dict id->(x,y), lat0, lon0)。"""
    lat0 = sum(s["lat"] for s in stations) / len(stations)
    lon0 = sum(s["lon"] for s in stations) / len(stations)
    c = math.cos(math.radians(lat0))
    xy = {}
    for s in stations:
        xy[s["id"]] = ((s["lon"] - lon0) * c * KM_PER_DEG,
                       (s["lat"] - lat0) * KM_PER_DEG)
    return xy, lat0, lon0


def compute_chords(stations, observations, velocity, pa_deg, time_offset):
    """把观测时刻换算为弦线与负观测约束点。"""
    xy, lat0, lon0 = station_xy(stations)
    th = math.radians(pa_deg)
    d = np.array([math.sin(th), math.cos(th)])   # 影子运动方向 = 弦线方向
    n = np.array([math.cos(th), -math.sin(th)])  # 弦线法向
    sta = {s["id"]: s for s in stations}

    pos = [o for o in observations if o["kind"] == "positive"]
    t_ref = (sum((o["t1"] + o["t2"]) / 2.0 for o in pos) / len(pos)) if pos else 0.0

    chords, negatives = [], []
    for o in observations:
        s = sta[o["station_id"]]
        p = np.array(xy[o["station_id"]])
        if o["kind"] == "positive":
            t1, t2 = o["t1"], o["t2"]
            e1 = o.get("err1") or 0.0
            e2 = o.get("err2") or 0.0
            t0 = (t1 + t2) / 2.0 + time_offset
            q = p - velocity * (t0 - t_ref) * d
            sig_t = math.hypot(e1, e2) / 2.0
            chords.append({
                "obs_id": o["id"], "station_id": s["id"], "station": s["name"],
                "excluded": bool(s["excluded"]),
                "qx": float(q[0]), "qy": float(q[1]),
                "half_len": velocity * (t2 - t1) / 2.0,
                "sig_mid": velocity * sig_t,          # 中点沿迹不确定 (km)
                "sig_len": velocity * sig_t,          # 半弦长不确定 (km)
                "duration": t2 - t1, "t_mid": t0,
            })
        else:
            t = o["t1"] + time_offset
            e = o.get("err1") or 0.0
            q = p - velocity * (t - t_ref) * d
            negatives.append({
                "obs_id": o["id"], "station_id": s["id"], "station": s["name"],
                "excluded": bool(s["excluded"]),
                "qx": float(q[0]), "qy": float(q[1]),
                "sig": velocity * max(e, 0.0), "t": t,
            })
    return {
        "chords": chords, "negatives": negatives,
        "t_ref": t_ref, "lat0": lat0, "lon0": lon0,
        "d": d.tolist(), "n": n.tolist(),
    }


# ---------------------------------------------------------------- 数值 LM

def _lm(func, p0, max_iter=300, tol=1e-13):
    """小型 Levenberg-Marquardt（数值雅可比），func 返回加权残差向量。"""
    p = np.array(p0, dtype=float)
    r = func(p)
    cost = float(r @ r)
    lam = 1e-3
    for _ in range(max_iter):
        J = np.empty((len(r), len(p)))
        for j in range(len(p)):
            h = 1e-7 * max(1.0, abs(p[j]))
            pj = p.copy(); pj[j] += h
            J[:, j] = (func(pj) - r) / h
        A = J.T @ J
        g = J.T @ r
        improved = False
        for _ in range(30):
            try:
                dp = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            pn = p + dp
            rn = func(pn)
            cn = float(rn @ rn)
            if cn < cost:
                p, r, cost = pn, rn, cn
                lam = max(lam / 4.0, 1e-12)
                improved = True
                break
            lam *= 4.0
        if not improved or np.max(np.abs(dp)) < tol * max(1.0, np.max(np.abs(p))):
            break
    # 末次雅可比（用于协方差）
    J = np.empty((len(r), len(p)))
    for j in range(len(p)):
        h = 1e-7 * max(1.0, abs(p[j]))
        pj = p.copy(); pj[j] += h
        J[:, j] = (func(pj) - r) / h
    return p, r, J, cost


def _param_sigmas(J, cost, n_par):
    dof = max(J.shape[0] - n_par, 1)
    try:
        cov = np.linalg.inv(J.T @ J) * (cost / dof)
        return np.sqrt(np.maximum(np.diag(cov), 0.0)).tolist()
    except np.linalg.LinAlgError:
        return [None] * n_par


# ---------------------------------------------------------------- 模型残差

def _circle_residuals(chords, d, n):
    def func(par):
        cx, cy, R = par
        c = np.array([cx, cy])
        out = []
        for ch in chords:
            q = np.array([ch["qx"], ch["qy"]])
            rel = q - c
            rho = float(n @ rel)              # 弦线到中心的垂直距离
            half = math.sqrt(max(R * R - rho * rho, 0.0))
            out.append(float(d @ rel) / max(ch["sig_mid"], 1e-6))
            out.append((ch["half_len"] - half) / max(ch["sig_len"], 1e-6))
        return np.array(out)
    return func


def _ellipse_frame(pa_deg):
    """a 轴方位角（自北向东）-> 两个轴单位矢量。"""
    ph = math.radians(pa_deg)
    A = np.array([math.sin(ph), math.cos(ph)])   # a 轴
    B = np.array([math.cos(ph), -math.sin(ph)])  # b 轴
    return A, B


def _ellipse_chord(q, c, a, b, A, B, d, n):
    """椭圆与弦线（过 q、方向 d、法向 n）的交点。"""
    rel = q - c
    rho = float(n @ rel)
    s_obs = float(d @ rel)
    n1, n2 = float(n @ A), float(n @ B)
    u1, u2 = float(d @ A), float(d @ B)
    ia2, ib2 = 1.0 / (a * a), 1.0 / (b * b)
    Aq = u1 * u1 * ia2 + u2 * u2 * ib2
    Bq = 2.0 * rho * (n1 * u1 * ia2 + n2 * u2 * ib2)
    Cq = rho * rho * (n1 * n1 * ia2 + n2 * n2 * ib2) - 1.0
    disc = Bq * Bq - 4.0 * Aq * Cq
    s_mid = -Bq / (2.0 * Aq)
    if disc >= 0.0:
        half = math.sqrt(disc) / (2.0 * Aq)
        miss = 0.0
    else:  # 弦线在椭圆之外：用脱靶距离把解往大处推
        half = 0.0
        miss = math.sqrt(-disc) / (2.0 * Aq)
    return s_obs, s_mid, half, miss


def _ellipse_residuals(chords, d, n):
    def func(par):
        cx, cy, a, b, phi = par
        a, b = abs(a), abs(b)
        c = np.array([cx, cy])
        A, B = _ellipse_frame(phi)
        out = []
        for ch in chords:
            q = np.array([ch["qx"], ch["qy"]])
            s_obs, s_mid, half, miss = _ellipse_chord(q, c, a, b, A, B, d, n)
            out.append((s_obs - s_mid) / max(ch["sig_mid"], 1e-6))
            out.append((ch["half_len"] - half - miss) / max(ch["sig_len"], 1e-6))
        return np.array(out)
    return func


def _inside_ellipse(point, c, a, b, phi):
    rel = np.array(point) - c
    A, B = _ellipse_frame(phi)
    return ((rel @ A) / a) ** 2 + ((rel @ B) / b) ** 2


# ---------------------------------------------------------------- 主入口

def run_fit(event, stations, observations, model, time_offset):
    """换算弦线并拟合轮廓，返回前端需要的全部结果。"""
    velocity = float(event["velocity"])
    pa = float(event["direction"])
    conv = compute_chords(stations, observations, velocity, pa, time_offset)
    chords, negatives = conv["chords"], conv["negatives"]
    d, n = np.array(conv["d"]), np.array(conv["n"])

    result = {
        "ok": False, "model": model, "time_offset": time_offset,
        "chords": chords, "negatives": negatives,
        "t_ref": conv["t_ref"], "lat0": conv["lat0"], "lon0": conv["lon0"],
        "d": conv["d"], "n": conv["n"],
        "fit": None, "residuals": [], "conflicts": [], "warnings": [],
    }

    used = [c for c in chords if not c["excluded"]]
    n_par = 3 if model == "circle" else 5
    if len(used) < (2 if model == "circle" else 3):
        result["warnings"].append(
            "有效弦线不足（%d 条），无法拟合%s" % (len(used), "圆形" if model == "circle" else "椭圆"))
        _eval_negatives(result, negatives, None)
        return result

    # 初值：弦线中点云的中心 + 平均视半径
    qs = np.array([[c["qx"], c["qy"]] for c in used])
    c0 = qs.mean(axis=0)
    R0 = float(np.mean([math.hypot(*(q - c0)) + c["half_len"]
                        for q, c in zip(qs, used)]))

    if model == "circle":
        func = _circle_residuals(used, d, n)
        p, r, J, cost = _lm(func, [c0[0], c0[1], max(R0, 1.0)])
        sig = _param_sigmas(J, cost, 3)
        fit = {
            "model": "circle",
            "cx": p[0], "cy": p[1], "R": abs(p[2]),
            "cx_sig": sig[0], "cy_sig": sig[1], "R_sig": sig[2],
            "a": abs(p[2]), "b": abs(p[2]), "phi": 0.0,
        }
    else:
        func_c = _circle_residuals(used, d, n)
        pc, _, _, _ = _lm(func_c, [c0[0], c0[1], max(R0, 1.0)])
        func = _ellipse_residuals(used, d, n)
        p, r, J, cost = _lm(func, [pc[0], pc[1], abs(pc[2]), abs(pc[2]), 0.0])
        a, b, phi = abs(p[2]), abs(p[3]), p[4] % 180.0
        if b > a:  # 约定 a >= b
            a, b, phi = b, a, (phi + 90.0) % 180.0
        sig = _param_sigmas(J, cost, 5)
        fit = {
            "model": "ellipse",
            "cx": p[0], "cy": p[1], "a": a, "b": b, "phi": phi,
            "cx_sig": sig[0], "cy_sig": sig[1],
            "a_sig": sig[2], "b_sig": sig[3], "phi_sig": sig[4],
            "R": math.sqrt(a * b),
        }

    n_used = len(used)
    dof = max(2 * n_used - n_par, 1)
    fit.update({
        "chi2": cost, "dof": dof, "chi2_red": cost / dof,
        "rms_km": math.sqrt(cost / max(2 * n_used, 1)) *
                  (sum(c["sig_mid"] for c in used) / n_used),
        "n_chords": n_used,
        "diameter_equiv": 2.0 * fit["R"],
    })
    if model == "ellipse":
        fit["flattening"] = (fit["a"] - fit["b"]) / fit["a"] if fit["a"] else 0.0
    result["fit"] = fit
    result["ok"] = True

    # 每条弦线的残差（km 与 σ）
    cvec = np.array([fit["cx"], fit["cy"]])
    for ch in chords:
        q = np.array([ch["qx"], ch["qy"]])
        rel = q - cvec
        if model == "circle":
            rho = float(n @ rel)
            half_pred = math.sqrt(max(fit["R"] ** 2 - rho * rho, 0.0))
            s_pred = 0.0
        else:
            A, B = _ellipse_frame(fit["phi"])
            _, s_pred, half_pred, _ = _ellipse_chord(
                q, cvec, fit["a"], fit["b"], A, B, d, n)
        res_mid = float(d @ rel) - s_pred
        res_len = ch["half_len"] - half_pred
        entry = {
            "obs_id": ch["obs_id"], "station": ch["station"],
            "excluded": ch["excluded"],
            "res_mid_km": res_mid, "res_mid_sig": res_mid / max(ch["sig_mid"], 1e-6),
            "res_len_km": res_len, "res_len_sig": res_len / max(ch["sig_len"], 1e-6),
        }
        if not ch["excluded"] and (abs(entry["res_mid_sig"]) > 3 or abs(entry["res_len_sig"]) > 3):
            entry["flag"] = "残差超过 3σ，建议检查计时或排除该站"
        result["residuals"].append(entry)

    _eval_negatives(result, negatives, fit)
    return result


def _eval_negatives(result, negatives, fit):
    """负观测与轮廓的相容性检查。"""
    for neg in negatives:
        neg.pop("inside", None); neg.pop("margin_km", None)
        if fit is None:
            continue
        c = (fit["cx"], fit["cy"])
        if fit["model"] == "circle":
            dist = math.hypot(neg["qx"] - c[0], neg["qy"] - c[1])
            inside = dist < fit["R"]
            margin = dist - fit["R"]
        else:
            val = _inside_ellipse((neg["qx"], neg["qy"]), c,
                                  fit["a"], fit["b"], fit["phi"])
            rn = math.sqrt(val)
            inside = rn < 1.0
            margin = (rn - 1.0) * (fit["a"] + fit["b"]) / 2.0  # 近似公里数
        neg["inside"] = bool(inside)
        neg["margin_km"] = margin
        if inside and not neg["excluded"]:
            result["conflicts"].append(
                "负观测站 %s 位于拟合轮廓内约 %.1f km，与“未发生掩星”矛盾"
                % (neg["station"], -margin))
