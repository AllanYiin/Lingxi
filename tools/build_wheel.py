#!/usr/bin/env python3
"""建置 Python wheel 的一鍵腳本：搬模型資產 → maturin build。

用法（在 repo 根目錄或任何位置執行皆可）：
    python tools/build_wheel.py [--convert] [--maturin-args "..."]

流程：
    1. 檢查 assets/*.bin 是否齊全；缺少時提示（或加 --convert 自動跑 lingxi-convert）
    2. 複製三個 .bin 到 crates/lingxi-py/python/lingxi/assets/（只在內容有變時覆蓋）
    3. maturin build --release，結束時印出 wheel 路徑
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

# repo 根目錄 = 本腳本所在目錄的上一層
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
PY_ASSETS = ROOT / "crates" / "lingxi-py" / "python" / "lingxi" / "assets"
PY_CRATE = ROOT / "crates" / "lingxi-py"
WHEELS = ROOT / "target" / "wheels"

REQUIRED = ["dict.bin", "hmm_bmes.bin", "hmm_pos.bin"]

# lingxi-convert 的預設輸入（舊版 LingXi 專案的相對位置）；--convert 時使用
OLD_RESOURCES = ROOT.parent / "LingXi" / "Resources"
OLD_MODELING = ROOT.parent / "ModelingData"


def run(cmd: list[str], cwd: Path) -> None:
    """執行指令，失敗即中止（輸出直通終端）。"""
    print(f"$ {' '.join(cmd)}  (cwd={cwd})")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"指令失敗（exit {result.returncode}）：{' '.join(cmd)}")


def ensure_assets(auto_convert: bool) -> None:
    """確認 assets/*.bin 齊全；缺少時依 --convert 決定自動轉換或報錯。"""
    missing = [n for n in REQUIRED if not (ASSETS / n).exists()]
    if not missing:
        return
    if not auto_convert:
        sys.exit(
            f"assets 缺少 {missing}。先跑 lingxi-convert，或加 --convert 讓本腳本代跑：\n"
            f"  cargo run --release -p lingxi-convert -- <Resources> <ModelingData> assets"
        )
    if not OLD_RESOURCES.exists():
        sys.exit(f"--convert 需要舊版資源目錄，但 {OLD_RESOURCES} 不存在")
    run(
        [
            "cargo", "run", "--release", "-p", "lingxi-convert", "--",
            str(OLD_RESOURCES), str(OLD_MODELING), str(ASSETS),
        ],
        cwd=ROOT,
    )
    still_missing = [n for n in REQUIRED if not (ASSETS / n).exists()]
    if still_missing:
        sys.exit(f"轉換後仍缺少 {still_missing}")


def sync_assets() -> None:
    """複製 .bin 到 wheel 的 package data 目錄；內容相同時跳過以保留 mtime。"""
    PY_ASSETS.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED:
        src = ASSETS / name
        dst = PY_ASSETS / name
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            print(f"  {name} 未變更，跳過")
            continue
        shutil.copy2(src, dst)
        print(f"  {name} → {dst.relative_to(ROOT)} ({src.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="搬模型資產並建置 lingxi Python wheel")
    parser.add_argument("--convert", action="store_true", help="assets 缺少時自動跑 lingxi-convert")
    parser.add_argument("--maturin-args", default="", help='附加給 maturin 的參數，如 "--interpreter python3.12"')
    args = parser.parse_args()

    if shutil.which("maturin") is None:
        sys.exit("找不到 maturin，先安裝：pip install maturin")

    ensure_assets(args.convert)
    print("同步模型資產：")
    sync_assets()

    cmd = ["maturin", "build", "--release", *args.maturin_args.split()]
    run(cmd, cwd=PY_CRATE)

    wheels = sorted(WHEELS.glob("lingxi-*.whl"), key=lambda p: p.stat().st_mtime)
    if wheels:
        print(f"\n完成：{wheels[-1]}")
        print(f"安裝：pip install --force-reinstall \"{wheels[-1]}\"")


if __name__ == "__main__":
    main()
