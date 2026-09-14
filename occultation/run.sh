#!/usr/bin/env bash
# 启动掩星弦线拟合台
export PYTHONUSERBASE="$(dirname "$0")/../.pyuser"
cd "$(dirname "$0")"
exec python3 app.py
