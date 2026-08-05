//! 無詞頻、多份且可分領域的自訂辭典來源格式。

use serde::{Deserialize, Serialize};

use crate::affect::AffectInput;

pub const CUSTOM_LEXICON_SCHEMA_VERSION: u16 = 1;
pub const MIN_CUSTOM_PRIORITY: i8 = -10;
pub const MAX_CUSTOM_PRIORITY: i8 = 10;
pub const CUSTOM_LEXICON_BASE_BIAS: f64 = 6.0;
pub const CUSTOM_LEXICON_PRIORITY_STEP: f64 = 0.5;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CustomLexiconEntry {
    pub word: String,
    #[serde(default)]
    pub pos: Option<String>,
    #[serde(default)]
    pub affect: Option<AffectInput>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CustomLexiconSpec {
    pub schema_version: u16,
    pub id: String,
    pub domain: String,
    #[serde(default)]
    pub priority: i8,
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub entries: Vec<CustomLexiconEntry>,
}

#[derive(Clone, Debug)]
pub struct SegmenterOptions {
    pub custom_lexicons: Vec<CustomLexiconSpec>,
    /// OOV token 首字的 log P(B/S | char) 特徵權重；預設 0.25，0 維持舊行為。
    pub reverse_emission_weight: f32,
}

impl Default for SegmenterOptions {
    fn default() -> Self {
        Self {
            custom_lexicons: Vec::new(),
            reverse_emission_weight: 0.25,
        }
    }
}
fn default_true() -> bool {
    true
}

pub fn parse_custom_lexicon(text: &str) -> Result<CustomLexiconSpec, String> {
    let spec: CustomLexiconSpec =
        serde_json::from_str(text).map_err(|error| format!("自訂辭典 JSON 無效: {error}"))?;
    validate_custom_lexicon(&spec)?;
    Ok(spec)
}

pub fn validate_custom_lexicon(spec: &CustomLexiconSpec) -> Result<(), String> {
    if spec.schema_version != CUSTOM_LEXICON_SCHEMA_VERSION {
        return Err(format!(
            "自訂辭典 schemaVersion {} 不受支援（目前為 {CUSTOM_LEXICON_SCHEMA_VERSION}）",
            spec.schema_version
        ));
    }
    if spec.id.trim().is_empty() {
        return Err("自訂辭典 id 不可為空".into());
    }
    if spec.domain.trim().is_empty() {
        return Err(format!("自訂辭典 {:?} 的 domain 不可為空", spec.id));
    }
    if !(MIN_CUSTOM_PRIORITY..=MAX_CUSTOM_PRIORITY).contains(&spec.priority) {
        return Err(format!(
            "自訂辭典 {:?} 的 priority {} 超出 {MIN_CUSTOM_PRIORITY}..={MAX_CUSTOM_PRIORITY}",
            spec.id, spec.priority
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_frequency_free_json_and_rejects_frequency_field() {
        let text = r#"{"schemaVersion":1,"id":"medical","domain":"medical","entries":[{"word":"冠狀動脈","pos":"Na"}]}"#;
        assert_eq!(parse_custom_lexicon(text).unwrap().priority, 0);
        let with_frequency = r#"{"schemaVersion":1,"id":"x","domain":"x","entries":[{"word":"甲乙","frequency":9}]}"#;
        assert!(parse_custom_lexicon(with_frequency).is_err());
    }

    #[test]
    fn reverse_emission_defaults_to_trident_selected_weight() {
        assert_eq!(SegmenterOptions::default().reverse_emission_weight, 0.25);
    }
}
