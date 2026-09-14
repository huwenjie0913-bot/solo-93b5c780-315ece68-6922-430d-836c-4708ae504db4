# 掩星弦线拟合台

小行星掩星观测后处理工具：把各站的消失/复现时刻换算为带不确定区间的弦线，
加权最小二乘拟合圆形/椭圆轮廓，管理负观测约束，保存方案快照并导出 JSON。

## 运行

```bash
./run.sh
```

首次启动会在项目内创建 `.venv` 虚拟环境并按 `requirements.txt`
（Flask、numpy）自动安装依赖；若当前 Python 环境已有所需依赖则直接启动。
也可手动安装后运行：

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

启动后访问 http://127.0.0.1:5000 。数据保存在同目录 `occultation.db`（SQLite）。

## 功能

- **事件管理**：新建事件，设置影子速度 (km/s) 与运动方向 PA（自北向东，度）。
- **站点与观测**：手填或 CSV 文本批量导入站点（经纬度）、正观测（消失/复现
  时刻与各自计时误差）、负观测（未掩时刻）。`载入示例数据` 可生成一组自洽
  的演示观测（真实轮廓 a=85, b=60, φ=25°）。
- **弦线换算**：时刻 → 局部切平面（x 东 / y 北，km）上的弦线；计时误差换算为
  中点与弦长不确定区间，画布上以浅色延伸和中点 tick 显示。
- **加权最小二乘拟合**：圆形（3 参数）或椭圆（5 参数），Levenberg-Marquardt，
  1/σ² 加权；输出中心、尺寸、方位角、约化 χ²、参数不确定度与逐站残差。
- **交互调整**：拖动统一时间偏移滑杆即时重拟合；点击画布上的弦线或勾选表格
  可排除/启用可疑站点；残差超过 3σ 自动标红。
- **负观测**：换算为过观测点、沿影子方向的整条“不相交弦线”（画布上以虚线
  表示）；圆形按圆心到直线距离、椭圆在归一化单位圆空间判断直线是否与轮廓
  相交，相交即以红色虚线弦与 ⚠ 标记醒目提示，并在结果区列出冲突说明。
- **光变曲线判读**：为任一站点导入“时间,流量,误差,曝光时长”CSV（时间支持
  HH:MM:SS.s 或秒数，可带表头），在画布上滚轮缩放、拖拽平移、框选掩星前后
  基线区间、单击/拖框屏蔽离群点，并预览常数/线性基线归一化结果；用曝光盒
  函数卷积的双阶跃模型（LM 加权拟合）解出消失 D、复现 R 时刻与不确定度，
  叠加模型曲线与残差；数据覆盖不足、参数不可辨识等失败情形会给出具体原因。
  每次判读可保存为版本（含基线区间、屏蔽点、拟合设置，服务端重算存档），
  勾选两个版本可比较时刻差异；确认版本后把 D/R 写入该站正观测——若该站存在
  手工填写或修改过的时刻则拒绝覆盖并说明原因，删除手工观测后方可写入。
- **方案快照**：任意调整可保存为快照；选择两个快照并排查看弦线图与参数差异表。
- **导出 JSON**：包含原始输入、弦线与拟合结果、异常说明（负观测冲突、超差残差）。

## CSV 导入格式

弦线拟合台批量导入：

```
STA, 站名, 纬度, 经度
POS, 站名, 消失HH:MM:SS.s, 复现HH:MM:SS.s, σ消失, σ复现
NEG, 站名, 观测HH:MM:SS.s, σ
```

光变曲线判读导入（每站一条，可带表头，支持逗号/分号/制表符分隔）：

```
time, flux, err, exp
03:14:00.00, 1.0012, 0.012, 0.18
03:14:00.20, 0.9987, 0.012, 0.18
```

时间列支持 HH:MM:SS.s 或秒数；误差、曝光时长缺省时分别以中位误差、
中位采样间隔代替。判读写入的正观测在观测表中带“判读”标记；手工修改
时刻后该观测转回 manual 来源，之后的版本确认不会再覆盖它。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/events` | 事件列表 / 新建 |
| GET/PUT/DELETE | `/api/events/<id>` | 事件状态 / 更新参数 / 删除 |
| POST | `/api/events/<id>/stations` | 添加站点 |
| PUT/DELETE | `/api/stations/<id>` | 更新（含 excluded）/ 删除 |
| POST | `/api/stations/<id>/observations` | 添加观测 |
| PUT/DELETE | `/api/observations/<id>` | 手工修改（转 manual 来源）/ 删除 |
| POST | `/api/events/<id>/import` | CSV 文本导入 |
| POST | `/api/events/<id>/demo` | 生成示例数据 |
| POST | `/api/events/<id>/fit` | 拟合（model, time_offset） |
| POST/GET/DELETE | `/api/events/<id>/snapshots`, `/api/snapshots/<id>` | 快照 |
| GET | `/api/events/<id>/export` | 导出 JSON |
| GET/POST | `/api/stations/<id>/lightcurves` | 光变曲线列表 / 导入 CSV |
| POST | `/api/stations/<id>/lightcurves/demo` | 生成示例光变曲线 |
| GET/DELETE | `/api/lightcurves/<id>` | 曲线详情（含版本）/ 删除 |
| POST | `/api/lightcurves/<id>/preview` | 归一化预览（基线区间+屏蔽点） |
| POST | `/api/lightcurves/<id>/fit` | 双阶跃拟合（模型曲线+残差+不确定度+失败原因） |
| POST | `/api/lightcurves/<id>/versions` | 保存判读版本（服务端重算存档） |
| GET/DELETE | `/api/lcversions/<id>` | 版本详情 / 删除 |
| POST | `/api/lcversions/<id>/confirm` | 确认版本：时刻写入该站正观测（保护手工时刻） |

## 文件

- `app.py` — Flask 路由与 SQLite 持久化
- `fitter.py` — 弦线换算、圆/椭圆加权拟合、负观测弦线约束检查
- `lightcurve.py` — 光变曲线判读：CSV 解析、基线归一化、曝光盒函数卷积
  双阶跃拟合、时刻不确定度与失败原因分析
- `requirements.txt` — Python 依赖声明（Flask、numpy）
- `run.sh` — 启动脚本（自动建虚拟环境并安装依赖）
- `static/index.html` / `style.css` / `app.js` — 单页前端
- `static/lightcurve.js` — 光变曲线判读前端（Canvas 交互、版本管理与比较）
