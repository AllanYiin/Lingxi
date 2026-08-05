# LingXi corpus pipeline

這個工具把開放的臺灣華語素材整理成「可人工覆核」的分詞／詞性資料，再由**已核准**資料產生 LingXi 目前轉換器可讀的 Dict、BMES HMM 與 POS HMM JSON。CKIPTagger 僅是預標註器，不是正解來源。

## 資料界線

`sources.toml` 固定資料集 revision、授權資訊與抽樣數量，目前包含：

- `twllm_real_prompts`：只取 `v1log-geminipro` 的真人 `human/user` 訊息；排除模型回覆、合成子集與命中 email／臺灣手機／身分證格式的疑似個資片段，發布前仍須人工掃描。
- `tw_law_current`：只取 `abandon_note` 為空的現行法條。
- `taiwan_patent_exam`：取臺灣專利師試題與選項，同一題固定落在同一 split。
- `wikinews_tw`：只取含臺灣地名／關鍵詞的報導，排除尚未解析的 `-{...}-` 標記；因授權鏈與繁化污染風險較高，只占 100 句內部試驗組。

下載資料、CKIP 模型與所有生成物都放在 `.corpus-work/`，不進版控。資料集與模型仍受各自授權條款約束；發布衍生資料前要另做授權與署名檢查。

## 安裝選用套件

Python 3.10 可用。抓取資料需要 `datasets` 與 Python 3.10 的 `tomli`；預標註另需 `ckiptagger` 及其模型資料：

```powershell
python -m pip install datasets tomli ckiptagger
```

CKIPTagger 模型資料較大，不由本工具自動下載。請依 [ckiptagger 官方說明](https://github.com/ckiplab/ckiptagger) 下載後，把模型目錄傳給 `--model-dir`。

## 1. 抓取、清洗、去重與固定切分

```powershell
python -m tools.corpus_pipeline prepare `
  --output .corpus-work/candidates.jsonl
```

快速驗證單一來源：

```powershell
python -m tools.corpus_pipeline prepare `
  --source tw_law_current `
  --count 20 `
  --output .corpus-work/law-smoke.jsonl
```

抽樣以固定 seed 與 SHA-256 排序；同一文件的所有片段一定落在同一 split。預設約為 train/dev/test = 98/1/1。

## 2. CKIPTagger 預標註

```powershell
python -m tools.corpus_pipeline preannotate `
  --input .corpus-work/candidates.jsonl `
  --output .corpus-work/preannotated.jsonl `
  --model-dir D:\models\ckiptagger
```

預設只處理 `train,dev`，刻意留下 `test` 做盲標。CKIPTagger 公開介面不提供逐詞 confidence，因此 JSONL 會記錄：

- CKIP 原始詞性；
- LingXi 建議詞性；
- 映射品質：`exact`、`broad`、`ambiguous`、`unmapped` 或 `skip`；
- 模稜兩可時的候選詞性，但不自動選一個。

`review.tokens` 會先填入可直接編修的建議。人工完成後把 `review.status` 改成 `accepted`，並填入 annotator。只有核准資料會進訓練統計。

```json
{
  "text": "我愛臺灣。",
  "split": "train",
  "review": {
    "status": "accepted",
    "annotator": "reviewer-01",
    "notes": "",
    "tokens": [
      {"text": "我", "pos": "r"},
      {"text": "愛", "pos": "v"},
      {"text": "臺灣", "pos": "ns"},
      {"text": "。", "pos": null}
    ]
  }
}
```

人工 token 的文字必須逐字、逐空白回拼成原始 `text`。繁簡、異體字或全半形不可在校正時偷偷改寫；文字正規化只能用於去重鍵。

## 3. 驗證人工校正

```powershell
python -m tools.corpus_pipeline validate `
  --input .corpus-work/reviewed.jsonl
```

驗證項目包括 schema、ID 唯一性、split、review 狀態、漢字 token 詞性與無損回拼。

## 4. 產生 Dict 與 HMM JSON

```powershell
python -m tools.corpus_pipeline build-model `
  --input .corpus-work/reviewed.jsonl `
  --output-dir .corpus-work/model-v1 `
  --min-support 10 `
  --min-confidence 0.8 `
  --min-margin 0.2
```

預設只用 `train`。輸出名稱與 `tools/lingxi-convert` 相容：

- `Dict.json`、空的 `VariantWords.json`、`Dict.evidence.jsonl`、`Dict.review.jsonl`（正式轉換時請帶回既有異體字表）
- `startProbs.json`、`transProbs.json`、`transProbs2.json`
- `emmitProbs.json`、`emmitProbs2.json`、`r_emmitProbs.json`
- `tagStartProbs.json`、`tagTransProbs.json`、`tagEmitProbs.json`
- `char_state_tab.json`、`training-report.json`

詞典門檻意義：

- `min-support`：詞至少出現幾次；
- `min-confidence`：第一名詞性次數 / 總次數；
- `min-margin`：（第一名次數 - 第二名次數）/ 總次數。

未過門檻的詞不會消失，而是連同完整 tag counts 進 review queue。這三個門檻只控制新詞典證據，和分詞階段可選的 POS rerank `top-k/信心接近程度` 是不同層次的參數。

## 評測建議

不要拿 CKIP 預標註直接當 gold。`test` 應由人工盲標，至少 10% 由兩人獨立標註並計算一致率；凍結後再同時測 LingXi、jieba 與 CKIPTagger。至少報告 segmentation precision/recall/F1、boundary F1、OOV recall，以及在相同斷詞下的 POS accuracy。模型選參只看 dev，不要反覆查看 test。

### 與 jieba 或其他工具互比

凍結人工 `test` 後，可選安裝 jieba 並產生預測：

```powershell
python -m pip install jieba
python -m tools.corpus_pipeline predict-jieba `
  --gold .corpus-work/reviewed.jsonl `
  --output .corpus-work/jieba-predictions.jsonl `
  --split test
```

再用同一評測器計分；`--train` 讓報告額外計算漢字詞 OOV recall：

```powershell
python -m tools.corpus_pipeline evaluate `
  --gold .corpus-work/reviewed.jsonl `
  --predictions .corpus-work/jieba-predictions.jsonl `
  --train .corpus-work/reviewed.jsonl `
  --split test
```

任何工具都可使用相同 prediction JSONL 介面；token 可是字串，若要算 POS accuracy 則使用含 `text`、`pos` 的物件：

```json
{"id": "gold-record-id", "tokens": [{"text": "臺灣", "pos": "ns"}, {"text": "華語", "pos": "n"}]}
```

POS accuracy 只在預測與 gold 斷詞完全相同的句子上計算，避免把斷詞錯誤混進詞性指標。
## 測試

```powershell
python -m unittest discover -s tools/corpus_pipeline/tests -v
```
