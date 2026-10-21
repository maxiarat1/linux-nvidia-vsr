#!/usr/bin/env bash
# Source this file. All writable runtime/cache paths stay in this project.
VSR_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export VSR_ROOT
export UV_CACHE_DIR="$VSR_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$VSR_ROOT/.cache/python"
export XDG_CACHE_HOME="$VSR_ROOT/.cache/xdg"
export CUDA_CACHE_PATH="$VSR_ROOT/.cache/cuda"
export CUPY_CACHE_DIR="$VSR_ROOT/.cache/cupy"
export __GL_SHADER_DISK_CACHE_PATH="$VSR_ROOT/.cache/gl"
export PYTHONPYCACHEPREFIX="$VSR_ROOT/.cache/pycache"
export TMPDIR="$VSR_ROOT/.cache/tmp"
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$CUPY_CACHE_DIR" "$__GL_SHADER_DISK_CACHE_PATH" "$VSR_ROOT/.state" "$VSR_ROOT/logs" "$VSR_ROOT/artifacts"
VSR_SITE="$VSR_ROOT/.venv/lib/python3.13/site-packages"
export CUDA_PATH="$VSR_SITE/nvidia/cu13"
export LD_LIBRARY_PATH="$VSR_SITE/nvidia/cu13/lib:$VSR_SITE/nvvfx/libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$VSR_ROOT${PYTHONPATH:+:$PYTHONPATH}"
