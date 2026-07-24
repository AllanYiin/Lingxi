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

from collections.abc import Iterable

from ._core import Segmenter, Token

__all__ = ["Segmenter", "Token", "load", "cut", "tokenize", "cut_batch", "extract_keywords"]

# wheel 內附模型目錄（maturin 將 python/lingxi/assets/ 打包進套件）。
_BUNDLED_ASSETS = Path(__file__).parent / "assets"


def load(
    asset_dir: str | os.PathLike | None = None,
    user_dict: str | os.PathLike | Iterable[str] | None = None,
) -> Segmenter:
    """建立分詞器。

    asset_dir 省略時依序找：環境變數 LINGXI_ASSETS → wheel 內附模型。
    user_dict 為自訂詞典：檔案路徑，或詞條行的可迭代物件。
    詞條格式（jieba 相容）：``詞 [頻率] [詞性]``，頻率省略時自動推定為
    恰好贏過現行切分的值，詞性省略時為 "n"。
    """
    if asset_dir is None:
        asset_dir = os.environ.get("LINGXI_ASSETS") or _BUNDLED_ASSETS
    lines: list[str] | None = None
    if user_dict is not None:
        if isinstance(user_dict, (str, os.PathLike)):
            lines = Path(user_dict).read_text(encoding="utf-8").splitlines()
        else:
            lines = list(user_dict)
    return Segmenter(str(asset_dir), lines)


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


def extract_keywords(
    text: str, top_k: int = 20, allow_tags: list[str] | None = None
) -> list[tuple[str, float]]:
    """惰性單例版 TextRank 關鍵字抽取 → [(詞, 權重)]，權重降冪。"""
    return _default_segmenter().extract_keywords(text, top_k, allow_tags)
