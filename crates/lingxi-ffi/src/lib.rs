//! C ABI binding。所有權規則：誰的 `_new` / `_tokenize` 就用對應的 `_free` 釋放；
//! token 不攜帶字串副本，只回傳指回呼叫者輸入緩衝的 byte 區間（零拷貝）。
//! Handle 內部為純函數分詞器，可多執行緒共享。
//!
//! 對應 header 見 include/lingxi.h（手寫維護，隨此檔同步修改）。

use std::ffi::{c_char, CStr, CString};

/// 將 C 端的 `(ptr, len)` 轉為 UTF-8。空輸入允許 NULL；非空輸入必須非 NULL。
///
/// # Safety
/// 非 NULL 時，`ptr` 必須指向至少 `len` bytes 的有效記憶體。
unsafe fn utf8_from_raw<'a>(ptr: *const u8, len: usize) -> Option<&'a str> {
    if len == 0 {
        return Some("");
    }
    if ptr.is_null() {
        return None;
    }
    std::str::from_utf8(std::slice::from_raw_parts(ptr, len)).ok()
}

/// 不透明 handle：分詞器 + 預先做好 NUL 結尾的詞性名稱表。
pub struct LingxiHandle {
    seg: lingxi_core::Segmenter,
    tag_cstrings: Vec<CString>,
}

/// 一個 token：呼叫者輸入緩衝內的 byte 區間 + 詞性 id。
#[repr(C)]
pub struct LingxiToken {
    pub byte_start: usize,
    pub byte_len: usize,
    pub tag: u8,
}

/// tokenize 結果：陣列 + 長度。以 lingxi_tokens_free 釋放。
#[repr(C)]
pub struct LingxiTokens {
    pub count: usize,
    pub items: *mut LingxiToken,
}

/// 從資產目錄建立分詞器；失敗回傳 NULL。
///
/// # Safety
/// `dir` 須為有效的 NUL 結尾 UTF-8 路徑字串。
#[no_mangle]
pub unsafe extern "C" fn lingxi_new_from_dir(dir: *const c_char) -> *mut LingxiHandle {
    lingxi_new_from_dir_ex(dir, std::ptr::null(), 0)
}

/// 從資產目錄建立分詞器並附加自訂詞典；失敗回傳 NULL。
///
/// # Safety
/// `dir` 須為有效 NUL 結尾 UTF-8；非空 user dictionary 指標須指向至少
/// `user_dict_len` bytes 的有效 UTF-8 緩衝。
#[no_mangle]
pub unsafe extern "C" fn lingxi_new_from_dir_ex(
    dir: *const c_char,
    user_dict_utf8: *const u8,
    user_dict_len: usize,
) -> *mut LingxiHandle {
    if dir.is_null() {
        return std::ptr::null_mut();
    }
    let Ok(dir) = CStr::from_ptr(dir).to_str() else {
        return std::ptr::null_mut();
    };
    let entries = if user_dict_utf8.is_null() || user_dict_len == 0 {
        Vec::new()
    } else {
        let bytes = std::slice::from_raw_parts(user_dict_utf8, user_dict_len);
        let Ok(text) = std::str::from_utf8(bytes) else {
            return std::ptr::null_mut();
        };
        lingxi_core::parse_user_dict(text)
    };
    match lingxi_core::Segmenter::from_asset_dir_with_user_dict(dir, &entries) {
        Ok(seg) => {
            let tag_cstrings = (0..=u8::MAX)
                .map_while(|index| {
                    let name = seg.try_tag_name(index)?;
                    Some(CString::new(name).expect("詞性名稱不含 NUL"))
                })
                .collect();
            Box::into_raw(Box::new(LingxiHandle { seg, tag_cstrings }))
        }
        Err(_) => std::ptr::null_mut(),
    }
}

/// 以 JSON array 載入多份結構化自訂辭典；不修改既有 constructor ABI。
///
/// # Safety
/// dir 必須指向有效 NUL 結尾 UTF-8 字串；lexicons_json_utf8 在長度非零時
/// 必須指向至少 lexicons_json_len bytes 的有效緩衝。
#[no_mangle]
pub unsafe extern "C" fn lingxi_new_from_dir_v2(
    dir: *const c_char,
    lexicons_json_utf8: *const u8,
    lexicons_json_len: usize,
) -> *mut LingxiHandle {
    if dir.is_null() {
        return std::ptr::null_mut();
    }
    let Ok(dir) = CStr::from_ptr(dir).to_str() else {
        return std::ptr::null_mut();
    };
    let Some(json) = utf8_from_raw(lexicons_json_utf8, lexicons_json_len) else {
        return std::ptr::null_mut();
    };
    let custom_lexicons = if json.is_empty() {
        Vec::new()
    } else {
        match serde_json::from_str(json) {
            Ok(value) => value,
            Err(_) => return std::ptr::null_mut(),
        }
    };
    match lingxi_core::Segmenter::from_asset_dir_with_options(
        dir,
        lingxi_core::SegmenterOptions { custom_lexicons },
    ) {
        Ok(seg) => {
            let tag_cstrings = (0..=u8::MAX)
                .map_while(|index| {
                    let name = seg.try_tag_name(index)?;
                    Some(CString::new(name).expect("詞性名稱不含 NUL"))
                })
                .collect();
            Box::into_raw(Box::new(LingxiHandle { seg, tag_cstrings }))
        }
        Err(_) => std::ptr::null_mut(),
    }
}

/// 釋放分詞器。
///
/// # Safety
/// `h` 須為 lingxi_new_from_dir 回傳且未曾釋放的指標；NULL 為 no-op。
#[no_mangle]
pub unsafe extern "C" fn lingxi_free(h: *mut LingxiHandle) {
    if !h.is_null() {
        drop(Box::from_raw(h));
    }
}

/// 分詞＋詞性。輸入為 UTF-8 位元組（不需 NUL 結尾）；
/// 非法 UTF-8 或 NULL 參數回傳 NULL。
///
/// # Safety
/// `utf8` 指向長度至少 `len` 的有效緩衝；回傳值以 lingxi_tokens_free 釋放。
/// token 的 byte 區間指向呼叫者的輸入緩衝，緩衝存活期間有效。
#[no_mangle]
pub unsafe extern "C" fn lingxi_tokenize(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
) -> *mut LingxiTokens {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let tokens: Vec<LingxiToken> = handle
        .seg
        .tokenize(text)
        .into_iter()
        .map(|t| LingxiToken {
            byte_start: t.byte_start,
            byte_len: t.byte_end - t.byte_start,
            tag: t.tag,
        })
        .collect();
    let boxed = tokens.into_boxed_slice();
    let count = boxed.len();
    let items = Box::into_raw(boxed) as *mut LingxiToken;
    Box::into_raw(Box::new(LingxiTokens { count, items }))
}

/// 釋放 tokenize 結果。
///
/// # Safety
/// `t` 須為 lingxi_tokenize 回傳且未曾釋放的指標；NULL 為 no-op。
#[no_mangle]
pub unsafe extern "C" fn lingxi_tokens_free(t: *mut LingxiTokens) {
    if t.is_null() {
        return;
    }
    let tokens = Box::from_raw(t);
    drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(
        tokens.items,
        tokens.count,
    )));
}

/// UTF-8 JSON 結果；data 為 NUL 結尾且 len 不含 NUL。
#[repr(C)]
pub struct LingxiUtf8 {
    pub len: usize,
    pub data: *mut c_char,
}

/// 分詞＋詞性＋情感，回傳 JSON array。
///
/// # Safety
/// h 必須為有效且尚未釋放的 handle；utf8 在長度非零時必須指向至少
/// len bytes 的有效 UTF-8 緩衝。回傳值須以 lingxi_utf8_free 釋放。
#[no_mangle]
pub unsafe extern "C" fn lingxi_annotate_json(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
) -> *mut LingxiUtf8 {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let value: Vec<_> = handle
        .seg
        .annotate(text)
        .into_iter()
        .map(|item| {
            let token = item.token;
            serde_json::json!({
                "word": &text[token.byte_start..token.byte_end],
                "tag": handle.seg.tag_name(token.tag),
                "byteStart": token.byte_start,
                "byteEnd": token.byte_end,
                "affect": item.affect,
                "source": item.source,
            })
        })
        .collect();
    let Ok(json) = serde_json::to_string(&value) else {
        return std::ptr::null_mut();
    };
    let len = json.len();
    let Ok(data) = CString::new(json) else {
        return std::ptr::null_mut();
    };
    Box::into_raw(Box::new(LingxiUtf8 {
        len,
        data: data.into_raw(),
    }))
}

/// 釋放 lingxi_annotate_json 結果。
///
/// # Safety
/// value 必須為 lingxi_annotate_json 回傳且尚未釋放的指標；NULL 為 no-op。
#[no_mangle]
pub unsafe extern "C" fn lingxi_utf8_free(value: *mut LingxiUtf8) {
    if value.is_null() {
        return;
    }
    let value = Box::from_raw(value);
    if !value.data.is_null() {
        drop(CString::from_raw(value.data));
    }
}

fn json_utf8(value: &serde_json::Value) -> *mut LingxiUtf8 {
    let Ok(json) = serde_json::to_string(value) else {
        return std::ptr::null_mut();
    };
    let len = json.len();
    let Ok(data) = CString::new(json) else {
        return std::ptr::null_mut();
    };
    Box::into_raw(Box::new(LingxiUtf8 {
        len,
        data: data.into_raw(),
    }))
}

/// 中文斷句，回傳含原句、byte offset 與句序的 JSON array。
///
/// # Safety
/// h 必須為有效 handle；utf8 在長度非零時必須指向有效 UTF-8 緩衝。
#[no_mangle]
pub unsafe extern "C" fn lingxi_split_sentences_json(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
    semicolon_boundary: bool,
) -> *mut LingxiUtf8 {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let value: Vec<_> = handle
        .seg
        .split_sentences_with_options(
            text,
            lingxi_core::SentenceSplitOptions { semicolon_boundary },
        )
        .into_iter()
        .map(|sentence| {
            serde_json::json!({
                "text": sentence.text,
                "byteStart": sentence.byte_start,
                "byteEnd": sentence.byte_end,
                "index": sentence.sentence_index,
            })
        })
        .collect();
    json_utf8(&serde_json::Value::Array(value))
}

/// 結構感知子句抽取，回傳原文、byte offset、句序與子句序的 JSON array。
///
/// # Safety
/// h 必須為有效 handle；utf8 在長度非零時必須指向有效 UTF-8 緩衝。
#[no_mangle]
pub unsafe extern "C" fn lingxi_split_clauses_json(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
) -> *mut LingxiUtf8 {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let value: Vec<_> = handle
        .seg
        .split_clauses(text)
        .into_iter()
        .map(|clause| {
            serde_json::json!({
                "text": clause.text,
                "byteStart": clause.byte_start,
                "byteEnd": clause.byte_end,
                "sentenceIndex": clause.sentence_index,
                "clauseIndex": clause.clause_index,
                "listItem": clause.list_item,
            })
        })
        .collect();
    json_utf8(&serde_json::Value::Array(value))
}

/// TextRank 抽取式摘要 JSON；使用 core 預設選項。
///
/// # Safety
/// h 必須為有效 handle；utf8 在長度非零時必須指向有效 UTF-8 緩衝。
#[no_mangle]
pub unsafe extern "C" fn lingxi_extract_summary_json(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
    top_k: usize,
) -> *mut LingxiUtf8 {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let value: Vec<_> = handle
        .seg
        .extract_summary(text, top_k)
        .into_iter()
        .map(|sentence| {
            serde_json::json!({
                "text": sentence.text,
                "byteStart": sentence.byte_start,
                "byteEnd": sentence.byte_end,
                "index": sentence.sentence_index,
                "clauseIndex": sentence.clause_index,
                "weight": sentence.weight,
                "explainability": sentence.explainability,
                "novelty": sentence.novelty,
                "coverageGain": sentence.coverage_gain,
                "signals": {
                    "properNounCount": sentence.signals.proper_noun_count,
                    "negationCount": sentence.signals.negation_count,
                    "emphasisCount": sentence.signals.emphasis_count,
                    "listItem": sentence.signals.list_item,
                    "objectNameCount": sentence.signals.object_name_count,
                    "dateCount": sentence.signals.date_count,
                    "numberCount": sentence.signals.number_count,
                    "quantityCount": sentence.signals.quantity_count,
                    "acronymCount": sentence.signals.acronym_count,
                },
            })
        })
        .collect();
    json_utf8(&serde_json::Value::Array(value))
}

/// 相鄰關鍵短語 JSON；使用 core 預設選項。
///
/// # Safety
/// h 必須為有效 handle；utf8 在長度非零時必須指向有效 UTF-8 緩衝。
#[no_mangle]
pub unsafe extern "C" fn lingxi_extract_keyphrases_json(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
    top_k: usize,
) -> *mut LingxiUtf8 {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let value: Vec<_> = handle
        .seg
        .extract_keyphrases(text, top_k)
        .into_iter()
        .map(|phrase| {
            let spans: Vec<_> = phrase
                .spans
                .into_iter()
                .map(|span| {
                    serde_json::json!({
                        "byteStart": span.byte_start,
                        "byteEnd": span.byte_end,
                    })
                })
                .collect();
            serde_json::json!({
                "phrase": phrase.phrase,
                "weight": phrase.weight,
                "occurrences": phrase.occurrences,
                "spans": spans,
            })
        })
        .collect();
    json_utf8(&serde_json::Value::Array(value))
}

/// 一個關鍵字：NUL 結尾 UTF-8 詞字串（結果持有，隨結果釋放）+ 權重。
#[repr(C)]
pub struct LingxiKeyword {
    pub word: *mut c_char,
    pub weight: f32,
}

/// 關鍵字抽取結果：陣列 + 長度。以 lingxi_keywords_free 釋放。
#[repr(C)]
pub struct LingxiKeywords {
    pub count: usize,
    pub items: *mut LingxiKeyword,
}

/// TextRank 關鍵字抽取，權重降冪，最多 `top_k` 個。
/// 非法 UTF-8 或 NULL 參數回傳 NULL。
///
/// # Safety
/// `utf8` 指向長度至少 `len` 的有效緩衝；回傳值以 lingxi_keywords_free 釋放。
#[no_mangle]
pub unsafe extern "C" fn lingxi_extract_keywords(
    h: *const LingxiHandle,
    utf8: *const u8,
    len: usize,
    top_k: usize,
) -> *mut LingxiKeywords {
    if h.is_null() {
        return std::ptr::null_mut();
    }
    let Some(text) = utf8_from_raw(utf8, len) else {
        return std::ptr::null_mut();
    };
    let handle = &*h;
    let keywords: Vec<LingxiKeyword> = handle
        .seg
        .extract_keywords(text, top_k)
        .into_iter()
        .map(|k| LingxiKeyword {
            // 詞來自詞典/正規化文字，不含 NUL；防禦性處理仍以 expect 標明前提。
            word: CString::new(k.word).expect("關鍵字不含 NUL").into_raw(),
            weight: k.weight,
        })
        .collect();
    let boxed = keywords.into_boxed_slice();
    let count = boxed.len();
    let items = Box::into_raw(boxed) as *mut LingxiKeyword;
    Box::into_raw(Box::new(LingxiKeywords { count, items }))
}

/// 釋放關鍵字抽取結果（含每個詞字串）。
///
/// # Safety
/// `k` 須為 lingxi_extract_keywords 回傳且未曾釋放的指標；NULL 為 no-op。
#[no_mangle]
pub unsafe extern "C" fn lingxi_keywords_free(k: *mut LingxiKeywords) {
    if k.is_null() {
        return;
    }
    let keywords = Box::from_raw(k);
    let items = Box::from_raw(std::ptr::slice_from_raw_parts_mut(
        keywords.items,
        keywords.count,
    ));
    for item in items.iter() {
        drop(CString::from_raw(item.word));
    }
}

/// 詞性 id → NUL 結尾名稱字串；id 越界回傳 NULL。
/// 回傳指標由 handle 持有，handle 存活期間有效，呼叫者不得釋放。
///
/// # Safety
/// `h` 須為有效 handle。
#[no_mangle]
pub unsafe extern "C" fn lingxi_tag_name(h: *const LingxiHandle, tag: u8) -> *const c_char {
    if h.is_null() {
        return std::ptr::null();
    }
    let handle = &*h;
    match handle.tag_cstrings.get(tag as usize) {
        Some(s) => s.as_ptr(),
        None => std::ptr::null(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn null_pointer_is_valid_only_for_empty_input() {
        unsafe {
            assert_eq!(utf8_from_raw(std::ptr::null(), 0), Some(""));
            assert_eq!(utf8_from_raw(std::ptr::null(), 1), None);
        }
    }
}
