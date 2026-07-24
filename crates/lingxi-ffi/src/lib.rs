//! C ABI binding。所有權規則：誰的 `_new` / `_tokenize` 就用對應的 `_free` 釋放；
//! token 不攜帶字串副本，只回傳指回呼叫者輸入緩衝的 byte 區間（零拷貝）。
//! Handle 內部為純函數分詞器，可多執行緒共享。
//!
//! 對應 header 見 include/lingxi.h（手寫維護，隨此檔同步修改）。

use std::ffi::{c_char, CStr, CString};

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
/// `user_dict_utf8` 為 jieba 格式詞典全文（每行 `詞 [頻率] [詞性]`），
/// 長度 `user_dict_len` bytes，不需 NUL 結尾；傳 NULL/0 表示無自訂詞典。
///
/// # Safety
/// `dir` 須為有效的 NUL 結尾 UTF-8 路徑字串；
/// `user_dict_utf8` 非 NULL 時須指向長度至少 `user_dict_len` 的有效緩衝。
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
            // 詞性表在載入時一次轉為 CString，之後 lingxi_tag_name 零成本。
            let tag_cstrings = (0..=u8::MAX)
                .map_while(|i| {
                    let name = seg.try_tag_name(i)?;
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
    if h.is_null() || (utf8.is_null() && len > 0) {
        return std::ptr::null_mut();
    }
    let bytes = std::slice::from_raw_parts(utf8, len);
    let Ok(text) = std::str::from_utf8(bytes) else {
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
    drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(tokens.items, tokens.count)));
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
    if h.is_null() || (utf8.is_null() && len > 0) {
        return std::ptr::null_mut();
    }
    let bytes = std::slice::from_raw_parts(utf8, len);
    let Ok(text) = std::str::from_utf8(bytes) else {
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
    let items = Box::from_raw(std::ptr::slice_from_raw_parts_mut(keywords.items, keywords.count));
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
