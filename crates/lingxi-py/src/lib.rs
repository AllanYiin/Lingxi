//! Python binding：`lingxi._core`。
//!
//! Python 層介面（見 python/lingxi/__init__.py 的薄包裝）：
//!   seg = lingxi.Segmenter()               # 預設載入 wheel 內附模型
//!   seg.cut("文字")                         # -> list[str]
//!   seg.tokenize("文字")                    # -> list[Token]，offset 為字元（code point）座標
//!   seg.cut_batch(texts)                   # rayon 平行，釋放 GIL
//!
//! byte offset → 字元 offset 的轉換在輸出時單趟完成，符合 Python 切片直覺。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

/// 帶詞性與字元位置的分詞結果。
#[pyclass(frozen, get_all)]
struct Token {
    /// 詞（原文切片的複本；跨 FFI 邊界必須複製）。
    word: String,
    /// 詞性名稱（統一詞性表）。
    tag: String,
    /// 起始字元（code point）位置。
    start: usize,
    /// 結束字元位置（exclusive）。
    end: usize,
}

#[pymethods]
impl Token {
    fn __repr__(&self) -> String {
        format!("Token({:?}, {:?}, {}, {})", self.word, self.tag, self.start, self.end)
    }
}

/// 分詞器。執行緒安全，可在多執行緒間共享。
#[pyclass(frozen)]
struct Segmenter {
    inner: lingxi_core::Segmenter,
}

#[pymethods]
impl Segmenter {
    /// 從資產目錄建立（目錄需含 dict.bin / hmm_bmes.bin / hmm_pos.bin）。
    /// `user_dict` 為 jieba 格式詞條行（`詞 [頻率] [詞性]`）；檔案讀取由
    /// Python 層包裝（見 __init__.py 的 load()）。
    #[new]
    #[pyo3(signature = (asset_dir, user_dict=None))]
    fn new(asset_dir: &str, user_dict: Option<Vec<String>>) -> PyResult<Self> {
        let entries = user_dict
            .map(|lines| lingxi_core::parse_user_dict(&lines.join("\n")))
            .unwrap_or_default();
        lingxi_core::Segmenter::from_asset_dir_with_user_dict(asset_dir, &entries)
            .map(|inner| Segmenter { inner })
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    /// TextRank 關鍵字抽取 → [(詞, 權重)]，權重降冪。
    /// `allow_tags` 指定候選詞性白名單；預設為名詞類/動詞/英文詞。
    #[pyo3(signature = (text, top_k=20, allow_tags=None))]
    fn extract_keywords(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<Vec<String>>,
    ) -> Vec<(String, f32)> {
        let tag_refs: Option<Vec<&str>> =
            allow_tags.as_ref().map(|v| v.iter().map(String::as_str).collect());
        self.inner
            .extract_keywords_with(text, top_k, tag_refs.as_deref())
            .into_iter()
            .map(|k| (k.word, k.weight))
            .collect()
    }

    /// 分詞 → 詞列表。
    fn cut(&self, text: &str) -> Vec<String> {
        self.inner.cut(text).into_iter().map(str::to_string).collect()
    }

    /// 分詞＋詞性 → Token 列表（start/end 為字元座標）。
    fn tokenize(&self, text: &str) -> Vec<Token> {
        tokens_of(&self.inner, text)
    }

    /// 批次分詞：釋放 GIL 並以 rayon 平行，輸出順序與輸入一致。
    fn cut_batch(&self, py: Python<'_>, texts: Vec<String>) -> Vec<Vec<String>> {
        py.allow_threads(|| {
            texts
                .par_iter()
                .map(|t| self.inner.cut(t).into_iter().map(str::to_string).collect())
                .collect()
        })
    }

    /// 批次分詞＋詞性：釋放 GIL 並以 rayon 平行。
    fn tokenize_batch(&self, py: Python<'_>, texts: Vec<String>) -> Vec<Vec<Token>> {
        py.allow_threads(|| texts.par_iter().map(|t| tokens_of(&self.inner, t)).collect())
    }
}

/// 分詞並轉為 Python Token（byte offset → 字元 offset 單趟轉換）。
fn tokens_of(seg: &lingxi_core::Segmenter, text: &str) -> Vec<Token> {
    let raw = seg.tokenize(text);
    let mut out = Vec::with_capacity(raw.len());
    // tokens 依 byte 位置遞增且無縫覆蓋，游標單趟前進即可換算字元位置。
    let mut cursor_byte = 0usize;
    let mut cursor_char = 0usize;
    for t in raw {
        cursor_char += text[cursor_byte..t.byte_start].chars().count();
        let word = &text[t.byte_start..t.byte_end];
        let char_len = word.chars().count();
        out.push(Token {
            word: word.to_string(),
            tag: seg.tag_name(t.tag).to_string(),
            start: cursor_char,
            end: cursor_char + char_len,
        });
        cursor_byte = t.byte_end;
        cursor_char += char_len;
    }
    out
}

/// Python 模組進入點。
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Segmenter>()?;
    m.add_class::<Token>()?;
    Ok(())
}
