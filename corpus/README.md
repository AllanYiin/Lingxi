# CKIP 全量語料建模

[process_corpus.py](./process_corpus.py) 會整合以下三個來源，逐筆執行 CKIPTagger 的 WS、POS、NER，再建立詞典、二階 BMES HMM 與詞性 HMM。標註與建模固定使用 CKIP 原生 POS（如 `Na`、`VC`、`VH`、`DE`）：

- `trident.load_examples_data("chinese").traindata.data.items`
- `D:\PycharmProjects\LingXi\ModelingData2\NewsData.txt`
- `D:\PycharmProjects\LingXi\ModelingData2\PttData.txt`

完整來源約 1,332 萬行。程式以 JSONL shards 逐批原子落盤；同一組參數重跑時會檢查並跳過完成的 shard。不同批次大小或來源組合必須使用新的 `--output-dir`，避免混合不相容的標註。

## 先決條件

- Python 3.10+
- `trident`、`ckiptagger` 與可用 TensorFlow backend
- CKIP 模型目錄；本 repo 預設為 `corpus/data`

## 先做 smoke test

Smoke test 必須使用獨立輸出目錄，避免被全量續跑誤用：

```powershell
python corpus/process_corpus.py all `
  --output-dir .corpus-work/ckip-smoke `
  --max-records-per-source 3 `
  --batch-size 3
```

預期會建立三個來源的 annotation shard，以及 `model/training-report.json`。

## 全量執行

```powershell
python corpus/process_corpus.py all `
  --output-dir .corpus-work/ckip `
  --batch-size 128
```

若要分階段執行：

```powershell
python corpus/process_corpus.py annotate --output-dir .corpus-work/ckip
python corpus/process_corpus.py build --output-dir .corpus-work/ckip
```

CPU 全量推論可能需要很長時間。已確認 CUDA 與 CKIPTagger 相容時才加 `--cuda`；中斷後使用完全相同命令即可續跑。

## 產物

`annotations/<source>/batch-*.jsonl` 保留每筆原文、CKIP 原始詞性、token 對應 NER 與完整實體 span；建模固定讀取 `ckip_pos`。

`model/` 內含：

- `Dict.json`：現有 LingXi converter 可讀取的 CKIP 單一主詞性詞典。
- `Dict.evidence.jsonl`：每詞完整 `pos_counts`、信心、margin 與實體證據；這是多詞性資訊的權威輸出。
- `Dict.review.jsonl`：未過門檻的低頻或多詞性歧義詞。
- `NamedEntities.jsonl`：專有名詞類型與次數。
- `startProbs.json`、`transProbs.json`、`transProbs2.json`、`emmitProbs.json`、`emmitProbs2.json`、`r_emmitProbs.json`：二階 BMES 模型。
- `tagStartProbs.json`、`tagTransProbs.json`、`tagTransProbs2.json`、`tagEmitProbs.json`：固定詞界的二階 joint-state POS 模型。
- `tagTransProbs2.json`：額外保存的二階詞性轉移；目前 `tools/lingxi-convert` 尚未接入此檔。
- `training-report.json`：來源筆數、token 數、NER 統計、門檻與 runtime 相容性。

## 詞典門檻與多詞性

預設一般詞需同時符合：

- `--min-support 10`：至少出現 10 次。
- `--min-pos-confidence 0.8`：主詞性占比至少 80%。
- `--min-pos-margin 0.2`：第一、第二詞性占比差至少 20%。

NER 詞另採 `--min-entity-support 2` 與 `--min-entity-confidence 0.8`，但仍須先符合總詞頻至少 10 次；NER 證據不得繞過共同的最低詞頻門檻。日期、金額、百分比等生成性實體只保留標註，不加入詞典，以免大量一次性數值污染詞段競爭。

`PosLexicon.json` 保留每個已知詞（含單字）的完整 `P(tag|word)`；runtime 在固定詞界上以全句二階 POS Viterbi 結合詞彙分布與跨詞轉移。OOV 才使用字元 joint-state HMM，POS 不會反向改動分詞。

## 驗證

```powershell
python -m unittest discover -s corpus/tests -v
python -m py_compile corpus/process_corpus.py corpus/model_training.py
```

CKIP 自動標註屬於 silver data，不是人工 gold data。正式替換 shipped model 前，仍應抽樣人工複核，並保留獨立 test split 做分詞與詞性評測。

## 清理

所有生成物都在指定的 `--output-dir`。確認不再需要續跑資料後，可由使用者自行刪除該目錄；程式不會自動刪除 annotations。
