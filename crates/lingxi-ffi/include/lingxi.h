/* LingXi 分詞 C ABI。與 src/lib.rs 手動同步維護。
 *
 * 所有權規則：
 *   - lingxi_new_from_dir 的結果以 lingxi_free 釋放
 *   - lingxi_tokenize 的結果以 lingxi_tokens_free 釋放
 *   - token 的 byte 區間指回呼叫者的輸入緩衝（零拷貝），緩衝存活期間有效
 *   - lingxi_tag_name 回傳的字串由 handle 持有，呼叫者不得釋放
 * handle 為純函數分詞器，可多執行緒共享。
 */
#ifndef LINGXI_H
#define LINGXI_H

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

/* 從資產目錄（含 dict.bin / hmm_bmes.bin / hmm_pos.bin）建立；失敗回 NULL。 */
LingxiHandle *lingxi_new_from_dir(const char *dir);

void lingxi_free(LingxiHandle *h);

/* 對 UTF-8 文字（長度 len，不需 NUL 結尾）分詞＋詞性；非法 UTF-8 回 NULL。 */
LingxiTokens *lingxi_tokenize(const LingxiHandle *h, const uint8_t *utf8, size_t len);

void lingxi_tokens_free(LingxiTokens *t);

/* 詞性 id → NUL 結尾名稱；id 越界回 NULL。 */
const char *lingxi_tag_name(const LingxiHandle *h, uint8_t tag);

#ifdef __cplusplus
}
#endif

#endif /* LINGXI_H */
