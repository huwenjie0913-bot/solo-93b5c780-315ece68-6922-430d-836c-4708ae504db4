"""光变曲线判读：归一化、曝光盒函数卷积双阶跃模型拟合。

流程约定：
  parse_csv   —— 解析 “时间,流量,误差,曝光时长” CSV（时间支持 HH:MM:SS.s 或秒）
  normalize   —— 用框选的掩星前后基线区间拟合基线（常数/线性）并归一化
  fit_curve   —— 双阶跃模型 level*(1-depth*(H(t-D)-H(t-R)))，逐点按各自曝光
                 时长做盒函数卷积（解析积分），Levenberg-Marquardt 加权拟合
                 消失时刻 D、复现时刻 R、深度与水平，输出时刻不确定度，
                 并对数据覆盖不足、参数不可辨识等失败情形给出具体原因。
"""
import math
import re

import numpy as np

from fitter import _lm


class LcError(ValueError):
    """输入数据问题（路由层以 400 返回）。"""


# ---------------------------------------------------------------- CSV 解析

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)\s*$")

_HEADER_ALIASES = {
    "time": {"time", "t", "jd", "hjd", "ut", "时刻", "时间"},
    "flux": {"flux", "f", "mag", "intensity", "counts", "流量", "强度"},
    "err": {"err", "error", "sigma", "flux_err", "误差", "不确定度"},
    "exp": {"exp", "exposure", "exptime", "exp_time", "dt", "曝光", "曝光时长", "曝光时间"},
}


def _parse_time(s):
    s = str(s).strip()
    m = _TIME_RE.match(s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    try:
        return float(s)
    except ValueError:
        raise LcError("无法解析时间 %r（支持 HH:MM:SS.s 或秒数）" % s)


def _split(line):
    for sep in (",", ";", "\t"):
        if sep in line:
            return [p.strip() for p in line.split(sep)]
    return line.split()


def parse_csv(text):
    """解析 CSV 文本 -> (series, errors)。

    series = {"t","flux","err","exp"}（按时间排序的 list）；
    errors 为被跳过行的说明。有效行不足时抛 LcError。
    """
    rows = []
    for ln, raw in enumerate((text or "").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        rows.append((ln, _split(s)))
    if not rows:
        raise LcError("CSV 内容为空")

    def looks_data(parts):
        try:
            _parse_time(parts[0])
            float(parts[1])
            return True
        except (LcError, ValueError, IndexError):
            return False

    col = {"time": 0, "flux": 1, "err": 2, "exp": 3}
    if not looks_data(rows[0][1]):
        names = [c.strip().lower() for c in rows.pop(0)[1]]
        for key, aliases in _HEADER_ALIASES.items():
            for i, nm in enumerate(names):
                if nm in aliases:
                    col[key] = i
                    break

    t, flux, err, exp = [], [], [], []
    errors = []
    for ln, parts in rows:
        try:
            if col["time"] >= len(parts) or col["flux"] >= len(parts):
                raise LcError("列数不足")
            ti = _parse_time(parts[col["time"]])
            fl = float(parts[col["flux"]])
            er = float(parts[col["err"]]) if col["err"] < len(parts) else 0.0
            ex = float(parts[col["exp"]]) if col["exp"] < len(parts) else 0.0
        except (ValueError, IndexError, LcError) as ex:
            errors.append("第 %d 行：%s" % (ln, ex))
            continue
        t.append(ti)
        flux.append(fl)
        err.append(er)
        exp.append(ex)
    if len(t) < 5:
        tail = ("；" + "；".join(errors[:3])) if errors else ""
        raise LcError("有效数据行不足（%d 行，至少 5 行）%s" % (len(t), tail))

    order = np.argsort(np.asarray(t, dtype=float))
    t = [float(t[i]) for i in order]
    flux = [float(flux[i]) for i in order]
    err = [float(err[i]) for i in order]
    exp = [float(exp[i]) for i in order]

    # 缺省曝光时长：中位采样间隔；缺省误差：中位正误差（全缺则等权 1.0）
    dt = np.diff(np.asarray(t))
    cad = float(np.median(dt)) if len(dt) else 1.0
    exp = [e if e > 0 else cad for e in exp]
    pos = [e for e in err if e > 0]
    er_def = float(np.median(pos)) if pos else 1.0
    err = [e if e > 0 else er_def for e in err]
    return {"t": t, "flux": flux, "err": err, "exp": exp}, errors


# ---------------------------------------------------------------- 基线设置

def _baselines_dict(b):
    """接受 {"pre":[a,b],"post":[c,d]} 或 [[a,b],[c,d]]，统一为 dict。"""
    if isinstance(b, dict):
        pre, post = b.get("pre"), b.get("post")
    else:
        lst = list(b or [])
        pre = lst[0] if len(lst) > 0 else None
        post = lst[1] if len(lst) > 1 else None

    def ok(iv):
        return iv and len(iv) == 2 and iv[1] > iv[0]

    return {"pre": [float(pre[0]), float(pre[1])] if ok(pre) else None,
            "post": [float(post[0]), float(post[1])] if ok(post) else None}


def _intervals(b):
    d = _baselines_dict(b)
    return [iv for iv in (d["pre"], d["post"]) if iv]


def normalize(series, baselines, masked, degree=1):
    """基线拟合 + 归一化。失败抛 LcError（原因可直接展示）。"""
    t = np.asarray(series["t"], dtype=float)
    flux = np.asarray(series["flux"], dtype=float)
    err = np.asarray(series["err"], dtype=float)
    intervals = _intervals(baselines)
    if not intervals:
        raise LcError("尚未框选基线区间：请在掩星前后各框选一段基线")
    sel = np.zeros(len(t), dtype=bool)
    for a, b in intervals:
        sel |= (t >= a) & (t <= b)
    drop = [int(i) for i in (masked or []) if 0 <= int(i) < len(t)]
    if drop:
        sel[np.array(drop, dtype=int)] = False
    idx = np.nonzero(sel)[0]
    deg = 1 if degree else 0
    need = deg + 2
    if len(idx) < need:
        raise LcError("基线区间内有效点不足（%d 个，至少 %d 个）："
                      "请扩大基线区间或减少屏蔽点" % (len(idx), need))
    t_ref = float(np.mean(t[idx]))
    x = t[idx] - t_ref
    w = 1.0 / np.maximum(err[idx], 1e-9)
    X = np.vstack([x ** k for k in range(deg + 1)]).T
    coeff, *_ = np.linalg.lstsq(X * w[:, None], flux[idx] * w, rcond=None)
    base = np.zeros_like(t)
    for k, c in enumerate(coeff):
        base += float(c) * (t - t_ref) ** k
    if np.any(base <= 0):
        raise LcError("基线拟合出现非正值，无法归一化：请检查基线区间")
    y = flux / base
    yerr = err / base
    rms = float(np.sqrt(np.mean(((flux[idx] - base[idx]) / base[idx]) ** 2)))
    return {
        "y": y.tolist(), "err": yerr.tolist(), "base": base.tolist(),
        "coeff": [float(c) for c in coeff], "t_ref": t_ref,
        "rms": rms, "n_base": int(len(idx)), "degree": deg,
        "intervals": [list(iv) for iv in intervals],
    }


# ---------------------------------------------------------------- 双阶跃模型

def model_flux(t, exp, D, R, depth, level):
    """曝光盒函数卷积的双阶跃模型（每个采样点按各自曝光窗解析平均）。

    未卷积信号 u(t) = level * (1 - depth * (H(t-D) - H(t-R)))；
    采样值为 u 在 [t-exp/2, t+exp/2] 上的平均，即
    level * (1 - depth * 曝光窗与[D,R]重叠长度 / exp)。
    """
    t = np.asarray(t, dtype=float)
    exp = np.asarray(exp, dtype=float)
    a = t - exp / 2.0
    b = t + exp / 2.0
    overlap = np.clip(np.minimum(b, R) - np.maximum(a, D), 0.0, None)
    frac = np.where(exp > 1e-12, overlap / np.maximum(exp, 1e-12),
                    ((t >= D) & (t < R)).astype(float))
    return level * (1.0 - depth * frac)


# ---------------------------------------------------------------- 拟合

def _jacobian(func, p, r, steps):
    """中心差分雅可比。步长需跨越曝光窗间隙造成的 χ² 平台，
    否则落在平台内时局部导数恒为零、协方差奇异。"""
    J = np.empty((len(r), len(p)))
    for j in range(len(p)):
        h = steps[j]
        pj = p.copy(); pj[j] += h
        pk = p.copy(); pk[j] -= h
        J[:, j] = (func(pj) - func(pk)) / (2.0 * h)
    return J


def fit_curve(series, baselines, masked, degree=1):
    """归一化 + 双阶跃拟合。返回完整结果（含失败原因，绝不抛 LcError 以外的异常）。"""
    t = np.asarray(series["t"], dtype=float)
    flux = np.asarray(series["flux"], dtype=float)
    err = np.asarray(series["err"], dtype=float)
    exp = np.asarray(series["exp"], dtype=float)
    n = len(t)
    bl = _baselines_dict(baselines)
    intervals = [iv for iv in (bl["pre"], bl["post"]) if iv]
    masked_idx = sorted({int(i) for i in (masked or []) if 0 <= int(i) < n})
    mset = set(masked_idx)
    used = np.array([i for i in range(n) if i not in mset], dtype=int)

    result = {
        "ok": False, "reasons": [], "warnings": [],
        "settings": {"baselines": bl, "masked": masked_idx,
                     "degree": 1 if degree else 0},
        "n_points": n, "n_masked": len(masked_idx),
    }

    if len(used) < 6:
        result["reasons"].append(
            "有效数据点不足（未屏蔽点 %d 个，至少需 6 个）" % len(used))
        return result
    try:
        norm = normalize(series, intervals, masked_idx, degree)
    except LcError as ex:
        result["reasons"].append(str(ex))
        return result

    y = np.asarray(norm["y"], dtype=float)
    yerr = np.asarray(norm["err"], dtype=float)
    tu, yu = t[used], y[used]
    eu = np.maximum(yerr[used], 1e-6)
    xu = exp[used]

    # ---- 初值：深度取 5% 分位，D/R 取半深度穿越点
    p5 = float(np.percentile(yu, 5.0))
    depth0 = min(max(1.0 - p5, 0.05), 0.98)
    thr = 1.0 - depth0 / 2.0
    below = np.nonzero(yu < thr)[0]
    if len(below):
        D0, R0 = float(tu[below[0]]), float(tu[below[-1]])
    else:
        i0 = int(np.argmin(yu))
        D0, R0 = float(tu[i0]) - 0.5, float(tu[i0]) + 0.5
    if R0 - D0 < 1e-3:
        R0 = D0 + max(float(np.median(np.diff(tu))), 0.1)

    def unpack(p):
        D = p[0]
        R = p[0] + math.exp(max(min(p[1], 20.0), -20.0))
        return D, R, p[2], p[3]

    def func(p):
        D, R, depth, level = unpack(p)
        return (model_flux(tu, xu, D, R, depth, level) - yu) / eu

    p0 = [D0, math.log(R0 - D0), depth0, 1.0]
    try:
        p, r, J, cost = _lm(func, p0)
    except Exception as ex:  # 数值异常（溢出等）
        result["reasons"].append("拟合过程出现数值异常：%s" % ex)
        return result
    D, R, depth, level = unpack(p)
    dur = R - D

    # ---- 协方差 -> 时刻不确定度（R = D + exp(log_dur) 做误差传播）。
    # LM 内部的雅可比步长太小，会落在曝光窗间隙的 χ² 平台上（导数为 0），
    # 这里用半采样间隔的中心差分重算，得到平滑后的曲率。
    dof = max(len(used) - 4, 1)
    cad = float(np.median(np.diff(tu))) if len(tu) > 1 else 1.0
    h_t = 0.5 * cad
    J = _jacobian(func, p, r, [h_t, h_t / max(dur, 1e-6), 1e-3, 1e-3])
    sig_D = sig_R = sig_dur = None
    try:
        cov = np.linalg.inv(J.T @ J) * (cost / dof)
        gR = np.array([1.0, dur, 0.0, 0.0])
        sig_D = math.sqrt(max(float(cov[0, 0]), 0.0))
        sig_R = math.sqrt(max(float(gR @ cov @ gR), 0.0))
        sig_dur = dur * math.sqrt(max(float(cov[1, 1]), 0.0))
        cond = float(np.linalg.cond(J.T @ J))
        if cond > 1e12:
            result["warnings"].append(
                "参数接近不可辨识：法方程条件数 %.2e，时刻不确定度仅供参考" % cond)
    except np.linalg.LinAlgError:
        result["reasons"].append(
            "参数不可辨识：正规方程奇异（通常是数据覆盖不足或模型退化）")

    # ---- 覆盖与可辨识性检查
    span0, span1 = float(t[0]), float(t[-1])
    if D < span0 - 1e-9 or R > span1 + 1e-9:
        result["reasons"].append(
            "数据覆盖不足：拟合的接触时刻超出数据范围"
            "（D=%.3f，R=%.3f，数据 %.3f–%.3f）" % (D, R, span0, span1))
    before = int(np.count_nonzero(tu + xu / 2.0 < D))
    after = int(np.count_nonzero(tu - xu / 2.0 > R))
    inside = int(np.count_nonzero((tu - xu / 2.0 < R) & (tu + xu / 2.0 > D)))
    if before == 0:
        result["reasons"].append(
            "数据覆盖不足：消失时刻之前没有可用数据点，无法约束 D 与基线")
    if after == 0:
        result["reasons"].append(
            "数据覆盖不足：复现时刻之后没有可用数据点，无法约束 R 与基线")
    if inside == 0:
        result["reasons"].append(
            "数据覆盖不足：掩星期间（含曝光窗）无采样点，深度与接触时刻不可辨识")
    if depth < 0.02:
        result["reasons"].append(
            "拟合深度仅 %.3f，未探测到可信掩星信号（信号过弱或时刻窗口错误）" % depth)
    elif depth > 1.2:
        result["warnings"].append(
            "拟合深度 %.2f 超过 1，基线归一化可能有误" % depth)
    med_exp = float(np.median(xu))
    if dur < med_exp:
        result["warnings"].append(
            "掩星时长 %.3f s 小于单个曝光时长 %.3f s，接触时刻依赖曝光模型外推"
            % (dur, med_exp))
    if sig_D is not None and sig_D > dur / 2.0:
        result["warnings"].append(
            "消失时刻不确定度（%.2f s）超过掩星时长一半，约束很弱" % sig_D)
    if sig_R is not None and sig_R > dur / 2.0:
        result["warnings"].append(
            "复现时刻不确定度（%.2f s）超过掩星时长一半，约束很弱" % sig_R)

    # ---- 输出（即使失败也返回曲线，便于复查判断过程）
    model_at = model_flux(t, exp, D, R, depth, level)
    resid = y - model_at
    rms = float(np.sqrt(np.mean(resid[used] ** 2)))
    dense_t = np.linspace(span0, span1, 400)
    dense_y = model_flux(dense_t, np.full_like(dense_t, med_exp),
                         D, R, depth, level)
    result.update({
        "ok": not result["reasons"],
        "D": float(D), "R": float(R), "dur": float(dur),
        "depth": float(depth), "level": float(level),
        "sig_D": sig_D, "sig_R": sig_R, "sig_dur": sig_dur,
        "chi2": float(cost), "dof": int(dof),
        "chi2_red": float(cost / dof), "rms": rms,
        "n_used": int(len(used)),
        "baseline": {k: norm[k] for k in
                     ("coeff", "t_ref", "rms", "n_base", "degree", "intervals")},
        "t": t.tolist(), "y": y.tolist(), "yerr": yerr.tolist(),
        "base": norm["base"],
        "model_t": dense_t.tolist(), "model_y": dense_y.tolist(),
        "model_at": model_at.tolist(), "resid": resid.tolist(),
        "masked": masked_idx,
    })
    return result


_TRIM_KEYS = ("ok", "reasons", "warnings", "D", "R", "dur", "depth", "level",
              "sig_D", "sig_R", "sig_dur", "chi2", "dof", "chi2_red", "rms",
              "n_used", "n_masked", "n_points", "baseline", "settings")


def trim_result(result):
    """存入版本库的精简结果（去掉逐点数组，保留标量、基线与设置）。"""
    return {k: result.get(k) for k in _TRIM_KEYS}


# ---------------------------------------------------------------- 示例数据

def demo_series(seed=20260914):
    """生成一条带噪声与离群点的示例光变曲线（归一化流量）。"""
    import random
    rng = random.Random(seed)
    t0 = 3 * 3600 + 14 * 60.0          # 03:14:00
    D_true, R_true, depth = t0 + 22.5, t0 + 29.1, 0.85
    cad, exp, noise = 0.2, 0.18, 0.012
    n = int(round(60.0 / cad))
    t = [t0 + i * cad for i in range(n)]
    flux = []
    for ti in t:
        m = float(model_flux(np.array([ti]), np.array([exp]),
                             D_true, R_true, depth, 1.0)[0])
        flux.append(m + rng.gauss(0.0, noise))
    for i in rng.sample(range(n), 4):  # 离群点，供练习屏蔽
        flux[i] += rng.choice((-1.0, 1.0)) * rng.uniform(0.08, 0.15)
    return {
        "t": t, "flux": flux,
        "err": [noise] * n, "exp": [exp] * n,
        "truth": {"D": D_true, "R": R_true, "depth": depth},
    }
