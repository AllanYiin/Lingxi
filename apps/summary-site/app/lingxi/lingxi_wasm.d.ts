/* tslint:disable */
/* eslint-disable */

/**
 * 分詞器（wasm 單執行緒環境；建構一次重複使用）。
 */
export class Segmenter {
    free(): void;
    [Symbol.dispose](): void;
    /**
     * 分詞＋詞性＋詞級情感（UTF-16 offset）。
     */
    annotate(text: string): any;
    /**
     * 分詞 → string[]。
     */
    cut(text: string): string[];
    /**
     * 相鄰關鍵短語 → [{phrase, weight, occurrences, spans}]。
     */
    extractKeyphrases(text: string, top_k: number): any;
    /**
     * 完整可設定的 TextRank 關鍵字抽取；stopwords 傳 string[]。
     */
    extractKeywordsConfigured(text: string, top_k: number, options: any): any;
    /**
     * schema v2 結構感知摘要。
     */
    extractSummary(text: string, max_blocks: number): any;
    /**
     * TextRank 關鍵字抽取 → [{word, weight}]，權重降冪。
     */
    extract_keywords(text: string, top_k: number): any;
    /**
     * 可設定專有名詞通道的關鍵字抽取。
     */
    extract_keywords_with_options(text: string, top_k: number, proper_noun_enabled: boolean, proper_noun_weight: number, proper_noun_max_ratio: number): any;
    /**
     * 結構化 factory：支援 optional affect.bin 與多份自訂辭典。
     */
    static fromAssets(dict: Uint8Array, bmes: Uint8Array, pos: Uint8Array, affect: Uint8Array | null | undefined, lexicons: any): Segmenter;
    /**
     * 以三個資產檔 bytes 建構（dict.bin / hmm_bmes.bin / hmm_pos.bin）。
     * `user_dict` 為選用的 jieba 格式自訂詞典全文（每行 `詞 [頻率] [詞性]`）。
     */
    constructor(dict: Uint8Array, bmes: Uint8Array, pos: Uint8Array, user_dict?: string | null);
    /**
     * 結構感知子句抽取 → [{text, start, end, sentenceIndex, clauseIndex}]。
     */
    splitClauses(text: string): any;
    /**
     * 中文斷句 → [{text, start, end, index}]，offset 為 UTF-16。
     */
    splitSentences(text: string, semicolon_boundary: boolean): any;
    /**
     * 分詞＋CKIP 原生詞性 → [{word, tag, start, end}]（UTF-16 offset）。
     */
    tokenize(text: string): any;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_segmenter_free: (a: number, b: number) => void;
    readonly segmenter_annotate: (a: number, b: number, c: number) => [number, number, number];
    readonly segmenter_cut: (a: number, b: number, c: number) => [number, number];
    readonly segmenter_extractKeyphrases: (a: number, b: number, c: number, d: number) => [number, number, number];
    readonly segmenter_extractKeywordsConfigured: (a: number, b: number, c: number, d: number, e: any) => [number, number, number];
    readonly segmenter_extractSummary: (a: number, b: number, c: number, d: number) => [number, number, number];
    readonly segmenter_extract_keywords: (a: number, b: number, c: number, d: number) => [number, number, number];
    readonly segmenter_extract_keywords_with_options: (a: number, b: number, c: number, d: number, e: number, f: number, g: number) => [number, number, number];
    readonly segmenter_fromAssets: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: any) => [number, number, number];
    readonly segmenter_new: (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number) => [number, number, number];
    readonly segmenter_splitClauses: (a: number, b: number, c: number) => [number, number, number];
    readonly segmenter_splitSentences: (a: number, b: number, c: number, d: number) => [number, number, number];
    readonly segmenter_tokenize: (a: number, b: number, c: number) => [number, number, number];
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_exn_store: (a: number) => void;
    readonly __externref_table_alloc: () => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __externref_drop_slice: (a: number, b: number) => void;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
