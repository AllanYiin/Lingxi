"""LingXi 繁體中文分詞與詞性標註（Rust 核心）。

用法::

    import lingxi
    seg = lingxi.load()          # 載入 wheel 內附模型；或 load(asset_dir=...)
    seg.cut("金管會前主委")       # -> list[str]
    seg.tokenize("...")          # -> list[Token(word, tag, start, end)]，字元座標
    lingxi.cut("...")            # 模組級便利函數（惰性單例）
"""
from __future__ import annotations

import os
from pathlib import Path

from ._core import Segmenter, Token

__all__ = ["Segmenter", "Token", "load", "cut", "tokenize", "cut_batch"]

# wheel 內附模型目錄（maturin 將 python/lingxi/assets/ 打包進套件）。
_BUNDLED_ASSETS = Path(__file__).parent / "assets"


def load(asset_dir: str | os.PathLike | None = None) -> Segmenter:
    """建立分詞器。asset_dir 省略時依序找：環境變數 LINGXI_ASSETS → wheel 內附模型。"""
    if asset_dir is None:
        asset_dir = os.environ.get("LINGXI_ASSETS") or _BUNDLED_ASSETS
    return Segmenter(str(asset_dir))


_default: Segmenter | None = None


def _default_segmenter() -> Segmenter:
    global _default
    if _default is None:
        _default = load()
    return _default


def cut(text: str) -> list[str]:
    """惰性單例版分詞。"""
    return _default_segmenter().cut(text)


def tokenize(text: str) -> list[Token]:
    """惰性單例版分詞＋詞性。"""
    return _default_segmenter().tokenize(text)


def cut_batch(texts: list[str]) -> list[list[str]]:
    """惰性單例版批次分詞（平行、釋放 GIL）。"""
    return _default_segmenter().cut_batch(texts)
