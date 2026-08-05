//! WASM/JS binding。
//!
//! 模型不內嵌（避免 .wasm 肥大），由 JS 端 fetch 三個資產檔後傳入：
//!
//! ```js
//! import init, { Segmenter } from "lingxi-wasm";
//! await init();
//! const seg = new Segmenter(dictBytes, bmesBytes, posBytes); // Uint8Array
//! // 或附自訂詞典（jieba 格式文字，每行 `詞 [頻率] [詞性]`）：
//! const seg2 = new Segmenter(dictBytes, bmesBytes, posBytes, "板南線 Nb\n柯文哲 Nb");
//! seg.cut("金管會前主委");                  // -> string[]
//! seg.tokenize("...");                     // -> [{word, tag: "Na"|"VC"|..., start, end}]，UTF-16 座標
//! seg.extract_keywords("...", 10);         // -> [{word, weight}]，權重降冪
//! ```

use serde::Serialize;
use wasm_bindgen::prelude::*;

/// JS 端的 token 形狀；offset 採 UTF-16 code unit（JS string 語意）。
#[derive(Serialize)]
struct JsToken {
    word: String,
    tag: String,
    start: u32,
    end: u32,
}

/// 分詞器（wasm 單執行緒環境；建構一次重複使用）。
#[wasm_bindgen]
pub struct Segmenter {
    inner: lingxi_core::Segmenter,
}

#[wasm_bindgen]
impl Segmenter {
    /// 以三個資產檔 bytes 建構（dict.bin / hmm_bmes.bin / hmm_pos.bin）。
    /// `user_dict` 為選用的 jieba 格式自訂詞典全文（每行 `詞 [頻率] [詞性]`）。
    #[wasm_bindgen(constructor)]
    pub fn new(
        dict: &[u8],
        bmes: &[u8],
        pos: &[u8],
        user_dict: Option<String>,
    ) -> Result<Segmenter, JsError> {
        let dict_model: lingxi_core::model::DictModel = lingxi_core::model::decode_asset(dict)
            .map_err(|error| JsError::new(&error.to_string()))?;
        let bmes_model: lingxi_core::model::BmesModel = lingxi_core::model::decode_asset(bmes)
            .map_err(|error| JsError::new(&error.to_string()))?;
        let pos_model: lingxi_core::model::PosModel = lingxi_core::model::decode_asset(pos)
            .map_err(|error| JsError::new(&error.to_string()))?;
        let entries = user_dict
            .map(|text| lingxi_core::parse_user_dict(&text))
            .unwrap_or_default();
        lingxi_core::Segmenter::from_models_with_user_dict(
            dict_model, bmes_model, pos_model, &entries,
        )
        .map(|inner| Segmenter { inner })
        .map_err(|error| JsError::new(&error.to_string()))
    }

    /// 結構化 factory：支援 optional affect.bin 與多份自訂辭典。
    #[wasm_bindgen(js_name = fromAssets)]
    pub fn from_assets(
        dict: &[u8],
        bmes: &[u8],
        pos: &[u8],
        affect: Option<Vec<u8>>,
        lexicons: JsValue,
    ) -> Result<Segmenter, JsError> {
        let dict_model: lingxi_core::model::DictModel = lingxi_core::model::decode_asset(dict)
            .map_err(|error| JsError::new(&error.to_string()))?;
        let bmes_model: lingxi_core::model::BmesModel = lingxi_core::model::decode_asset(bmes)
            .map_err(|error| JsError::new(&error.to_string()))?;
        let pos_model: lingxi_core::model::PosModel = lingxi_core::model::decode_asset(pos)
            .map_err(|error| JsError::new(&error.to_string()))?;
        let affect_model = affect
            .as_deref()
            .map(lingxi_core::model::decode_asset)
            .transpose()
            .map_err(|error| JsError::new(&error.to_string()))?;
        let custom_lexicons = if lexicons.is_null() || lexicons.is_undefined() {
            Vec::new()
        } else {
            serde_wasm_bindgen::from_value(lexicons)
                .map_err(|error| JsError::new(&format!("自訂辭典資料無效: {error}")))?
        };
        lingxi_core::Segmenter::from_models_with_options(
            dict_model,
            bmes_model,
            pos_model,
            affect_model,
            lingxi_core::SegmenterOptions { custom_lexicons },
        )
        .map(|inner| Segmenter { inner })
        .map_err(|error| JsError::new(&error.to_string()))
    }

    /// TextRank 關鍵字抽取 → [{word, weight}]，權重降冪。
    pub fn extract_keywords(&self, text: &str, top_k: usize) -> Result<JsValue, JsError> {
        #[derive(Serialize)]
        struct JsKeyword {
            word: String,
            weight: f32,
        }
        let out: Vec<JsKeyword> = self
            .inner
            .extract_keywords(text, top_k)
            .into_iter()
            .map(|k| JsKeyword {
                word: k.word,
                weight: k.weight,
            })
            .collect();
        serde_wasm_bindgen::to_value(&out).map_err(|e| JsError::new(&e.to_string()))
    }

    /// 可設定專有名詞通道的關鍵字抽取。
    pub fn extract_keywords_with_options(
        &self,
        text: &str,
        top_k: usize,
        proper_noun_enabled: bool,
        proper_noun_weight: f32,
        proper_noun_max_ratio: f32,
    ) -> Result<JsValue, JsError> {
        #[derive(Serialize)]
        struct JsKeyword {
            word: String,
            weight: f32,
        }
        let out: Vec<JsKeyword> = self
            .inner
            .extract_keywords_with_options(
                text,
                top_k,
                None,
                lingxi_core::KeywordOptions {
                    proper_noun_enabled,
                    proper_noun_weight,
                    proper_noun_max_ratio,
                },
            )
            .into_iter()
            .map(|keyword| JsKeyword {
                word: keyword.word,
                weight: keyword.weight,
            })
            .collect();
        serde_wasm_bindgen::to_value(&out).map_err(|e| JsError::new(&e.to_string()))
    }

    /// 分詞 → string[]。
    pub fn cut(&self, text: &str) -> Vec<String> {
        self.inner
            .cut(text)
            .into_iter()
            .map(str::to_string)
            .collect()
    }

    /// 分詞＋詞性＋詞級情感（UTF-16 offset）。
    pub fn annotate(&self, text: &str) -> Result<JsValue, JsError> {
        #[derive(Serialize)]
        struct JsAnnotatedToken {
            word: String,
            tag: String,
            start: u32,
            end: u32,
            #[serde(skip_serializing_if = "Option::is_none")]
            affect: Option<lingxi_core::AffectAnnotation>,
            #[serde(skip_serializing_if = "Option::is_none")]
            source: Option<lingxi_core::LexiconSource>,
        }
        let raw = self.inner.annotate(text);
        let mut out = Vec::with_capacity(raw.len());
        let mut cursor_byte = 0usize;
        let mut cursor_u16 = 0usize;
        for item in raw {
            let token = item.token;
            cursor_u16 += utf16_len(&text[cursor_byte..token.byte_start]);
            let word = &text[token.byte_start..token.byte_end];
            let word_u16 = utf16_len(word);
            out.push(JsAnnotatedToken {
                word: word.to_string(),
                tag: self.inner.tag_name(token.tag).to_string(),
                start: cursor_u16 as u32,
                end: (cursor_u16 + word_u16) as u32,
                affect: item.affect,
                source: item.source,
            });
            cursor_byte = token.byte_end;
            cursor_u16 += word_u16;
        }
        serde_wasm_bindgen::to_value(&out).map_err(|error| JsError::new(&error.to_string()))
    }

    /// 分詞＋CKIP 原生詞性 → [{word, tag, start, end}]（UTF-16 offset）。
    pub fn tokenize(&self, text: &str) -> Result<JsValue, JsError> {
        let raw = self.inner.tokenize(text);
        let mut out = Vec::with_capacity(raw.len());
        // tokens 無縫遞增覆蓋，單趟游標把 byte offset 換算為 UTF-16 offset。
        let mut cursor_byte = 0usize;
        let mut cursor_u16 = 0usize;
        for t in raw {
            cursor_u16 += utf16_len(&text[cursor_byte..t.byte_start]);
            let word = &text[t.byte_start..t.byte_end];
            let w16 = utf16_len(word);
            out.push(JsToken {
                word: word.to_string(),
                tag: self.inner.tag_name(t.tag).to_string(),
                start: cursor_u16 as u32,
                end: (cursor_u16 + w16) as u32,
            });
            cursor_byte = t.byte_end;
            cursor_u16 += w16;
        }
        serde_wasm_bindgen::to_value(&out).map_err(|e| JsError::new(&e.to_string()))
    }
}

/// 字串的 UTF-16 code unit 數。
fn utf16_len(s: &str) -> usize {
    s.chars().map(char::len_utf16).sum()
}
