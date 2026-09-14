# 掩星弦线拟合台

小行星掩星观测后处理工具：把各站的消失/复现时刻换算为带不确定区间的弦线，
加权最小二乘拟合圆形/椭圆轮廓，管理负观测约束，保存方案快照并导出 JSON。

## 运行

```bash
./run.sh          # 或：PYTHONUSERBASE=/workspace/.pyuser python3 app.py
```

依赖 Flask 与 numpy（已安装在 `/workspace/.pyuser`，脚本会自动设置
`PYTHONUSERBASE`）。启动后访问 http://127.0.0.1:5000 。
数据保存在同目录 `occultation.db`（SQLite）。

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
- **负观测**：显示为 ⊘ 标记；落入拟合轮廓内即红色高亮并在结果区列出冲突说明。
- **方案快照**：任意调整可保存为快照；选择两个快照并排查看弦线图与参数差异表。
- **导出 JSON**：包含原始输入、弦线与拟合结果、异常说明（负观测冲突、超差残差）。

## CSV 导入格式

```
STA, 站名, 纬度, 经度
POS, 站名, 消失HH:MM:SS.s, 复现HH:MM:SS.s, σ消失, σ复现
NEG, 站名, 观测HH:MM:SS.s, σ
```

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/events` | 事件列表 / 新建 |
| GET/PUT/DELETE | `/api/events/<id>` | 事件状态 / 更新参数 / 删除 |
| POST | `/api/events/<id>/stations` | 添加站点 |
| PUT/DELETE | `/api/stations/<id>` | 更新（含 excluded）/ 删除 |
| POST | `/api/stations/<id>/observations` | 添加观测 |
| POST | `/api/events/<id>/import` | CSV 文本导入 |
| POST | `/api/events/<id>/demo` | 生成示例数据 |
| POST | `/api/events/<id>/fit` | 拟合（model, time_offset） |
| POST/GET/DELETE | `/api/events/<id>/snapshots`, `/api/snapshots/<id>` | 快照 |
| GET | `/api/events/<id>/export` | 导出 JSON |

## 文件

- `app.py` — Flask 路由与 SQLite 持久化
- `fitter.py` — 弦线换算、圆/椭圆加权拟合、负观测相容性检查
- `static/index.html` / `style.css` / `app.js` — 单页前端
