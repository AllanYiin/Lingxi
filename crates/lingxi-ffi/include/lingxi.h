/* LingXi 分詞 C ABI。與 src/lib.rs 手動同步維護。
 *
 * 所有權規則：
 *   - lingxi_new_from_dir / lingxi_new_from_dir_ex 的結果以 lingxi_free 釋放
 *   - lingxi_tokenize 的結果以 lingxi_tokens_free 釋放
 *   - lingxi_extract_keywords 的結果以 lingxi_keywords_free 釋放
 *   - token 的 byte 區間指回呼叫者的輸入緩衝（零拷貝），緩衝存活期間有效
 *   - lingxi_tag_name 回傳的字串由 handle 持有，呼叫者不得釋放
 * handle 為純函數分詞器，可多執行緒共享。
 */
#ifndef LINGXI_H
#define LINGXI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct LingxiHandle LingxiHandle; /* 不透明 */

typedef struct LingxiToken {
    size_t byte_start; /* 於輸入緩衝的起始 byte */
    size_t byte_len;   /* 詞的 byte 長度 */
    uint8_t tag;       /* 詞性 id，經 lingxi_tag_name 取名稱 */
} LingxiToken;

typedef struct LingxiTokens {
    size_t count;
    LingxiToken *items;
} LingxiTokens;

typedef struct LingxiKeyword {
    char *word;   /* NUL 結尾 UTF-8，由結果持有 */
    float weight; /* TextRank 權重（降冪排列） */
} LingxiKeyword;

typedef struct LingxiKeywords {
    size_t count;
    LingxiKeyword *items;
} LingxiKeywords;

typedef struct LingxiUtf8 {
    size_t len;
    char *data;
} LingxiUtf8;

/* 從資產目錄（含 dict.bin / hmm_bmes.bin / hmm_pos.bin）建立；失敗回 NULL。 */
LingxiHandle *lingxi_new_from_dir(const char *dir);

/* 同上，並附加自訂詞典：jieba 格式全文（每行「詞 [頻率] [詞性]」），
 * user_dict_len bytes，不需 NUL 結尾；NULL/0 表示無自訂詞典。 */
LingxiHandle *lingxi_new_from_dir_ex(const char *dir,
                                     const uint8_t *user_dict_utf8,
                                     size_t user_dict_len);

/* 載入 CustomLexiconSpec JSON array；NULL/0 表示空 array。 */
LingxiHandle *lingxi_new_from_dir_v2(const char *dir,
                                     const uint8_t *lexicons_json_utf8,
                                     size_t lexicons_json_len);

void lingxi_free(LingxiHandle *h);

/* 對 UTF-8 文字（長度 len，不需 NUL 結尾）分詞＋詞性；非法 UTF-8 回 NULL。 */
LingxiTokens *lingxi_tokenize(const LingxiHandle *h, const uint8_t *utf8, size_t len);

void lingxi_tokens_free(LingxiTokens *t);

/* 詞級情感標註 JSON；結果以 lingxi_utf8_free 釋放。 */
LingxiUtf8 *lingxi_annotate_json(const LingxiHandle *h,
                                 const uint8_t *utf8, size_t len);
void lingxi_utf8_free(LingxiUtf8 *value);

/* 中文斷句 JSON：[{text, byteStart, byteEnd, index}]。 */
LingxiUtf8 *lingxi_split_sentences_json(const LingxiHandle *h,
                                        const uint8_t *utf8, size_t len,
                                        bool semicolon_boundary);

/* 結構感知子句抽取 JSON；保留 byte offset、sentenceIndex 與 clauseIndex。 */
LingxiUtf8 *lingxi_split_clauses_json(const LingxiHandle *h,
                                      const uint8_t *utf8, size_t len);

/* TextRank 抽取式摘要 JSON；使用 core 預設選項。 */
LingxiUtf8 *lingxi_extract_summary_json(const LingxiHandle *h,
                                        const uint8_t *utf8, size_t len,
                                        size_t top_k);

/* 相鄰關鍵短語 JSON；使用 core 預設選項。 */
LingxiUtf8 *lingxi_extract_keyphrases_json(const LingxiHandle *h,
                                           const uint8_t *utf8, size_t len,
                                           size_t top_k);

/* TextRank 關鍵字抽取，權重降冪，最多 top_k 個；非法 UTF-8 回 NULL。 */
LingxiKeywords *lingxi_extract_keywords(const LingxiHandle *h,
                                        const uint8_t *utf8, size_t len,
                                        size_t top_k);

void lingxi_keywords_free(LingxiKeywords *k);

/* 詞性 id → NUL 結尾名稱；id 越界回 NULL。 */
const char *lingxi_tag_name(const LingxiHandle *h, uint8_t tag);

#ifdef __cplusplus
}
#endif

#endif /* LINGXI_H */
