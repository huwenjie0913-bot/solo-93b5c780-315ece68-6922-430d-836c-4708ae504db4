#!/usr/bin/env bash
# 启动掩星弦线拟合台。
# 全新环境：自动在项目内创建 .venv 并安装 requirements.txt；
# 若系统 Python 已有所需依赖则直接启动。不再依赖任何预先填充的目录。
set -e
cd "$(dirname "$0")"

# 已安装依赖（如系统级或已激活的环境）则直接启动
if python3 -c "import flask, numpy" 2>/dev/null; then
  exec python3 app.py
fi

# 创建项目内虚拟环境（无 ensurepip 的系统退化为 --without-pip）
if [ ! -x .venv/bin/python ]; then
  echo "创建虚拟环境 .venv ..."
  python3 -m venv .venv 2>/dev/null || python3 -m venv --without-pip .venv
fi
VPY=.venv/bin/python

# 确保 venv 内有 pip
if ! "$VPY" -m pip --version >/dev/null 2>&1; then
  echo "引导 pip ..."
  GETPIP=$(mktemp /tmp/get-pip.XXXXXX.py)
  curl -sS https://bootstrap.pypa.io/get-pip.py -o "$GETPIP"
  "$VPY" "$GETPIP"
  rm -f "$GETPIP"
fi

echo "安装依赖 requirements.txt ..."
"$VPY" -m pip install -q -r requirements.txt

exec "$VPY" app.py
