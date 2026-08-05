# Ultra-light punctuation model

這個工具以 NFC Unicode 字元邊界為輸出格點，訓練一個小型的
`depthwise dilated Conv1D → BiGRU` 模型。模型有兩個輸出頭：八類標點與
`NONE / OPEN / CLOSE` 引號動作；推論時以深度一有限狀態解碼保證引號成對。

## 資料格式

輸入是 UTF-8 JSONL，每列至少包含保留原始標點的 `text`。同一檔案可用
`split` 區分 train、dev、test：

```json
{"id":"train-1","split":"train","text":"他說：「你好。」"}
{"id":"dev-1","split":"dev","text":"今天下雨，記得帶傘。"}
{"id":"test-1","split":"test","text":"你明天會來嗎？"}
```

若資料來自 `tools.corpus_pipeline`，加上 `--require-accepted` 後只會使用
`review.status == "accepted"` 的資料，並在 `review.tokens` 能無損對齊時自動產生
BMES 與詞長特徵。沒有人工 token 時，模型使用 unknown 分詞特徵，字元輸出格點
不受影響。

URL、Email、小數與千分位中的內部符號會保留為輸入，不會當成待補標點。V1
不支援巢狀引號、混合 `?!` 或同一邊界的多個引號動作；不合法資料會被跳過並在
建字表／類別統計報告中計數。

## 訓練

同一個 JSONL 以 `split` 切分：

```powershell
python -m tools.punctuation_model train `
  --train .corpus-work/punctuation.jsonl `
  --dev .corpus-work/punctuation.jsonl `
  --output-dir .corpus-work/punctuation-model `
  --epochs 10 `
  --batch-size 64 `
  --device auto
```

train/dev 已分成不同檔案且沒有 `split` 欄位時：

```powershell
python -m tools.punctuation_model train `
  --train .corpus-work/train.jsonl `
  --train-split none `
  --dev .corpus-work/dev.jsonl `
  --dev-split none `
  --output-dir .corpus-work/punctuation-model
```

預設架構：

```yaml
character_vocab: 4096
hash_buckets: 1024
model_width: 48
conv_dilations: [1, 2, 4, 8]
gru_hidden_per_direction: 64
punctuation_classes: 8
quote_classes: 3
```

產物包括：

- `best.pt`：依 dev punctuation macro-F1 選出的 checkpoint。
- `last.pt`：最後一個 epoch。
- `training-report.json`：設定、參數量、字表／類別統計與每輪指標。

快速 smoke training 可限制 step：

```powershell
python -m tools.punctuation_model train `
  --train .corpus-work/punctuation.jsonl `
  --dev .corpus-work/punctuation.jsonl `
  --output-dir .corpus-work/punctuation-smoke `
  --epochs 1 `
  --steps-per-epoch 2 `
  --eval-batches 1 `
  --batch-size 2
```

## 評估

```powershell
python -m tools.punctuation_model evaluate `
  --checkpoint .corpus-work/punctuation-model/best.pt `
  --input .corpus-work/punctuation.jsonl `
  --split test `
  --predictions .corpus-work/punctuation-predictions.jsonl `
  --style zh-tw
```

報告包含每類 precision／recall／F1、非 `NONE` macro-F1、引號序列 exact
match、未約束 greedy 解碼的未閉合率，以及約束解碼後固定為零的未閉合率。

檢查 checkpoint 大小與模型設定：

```powershell
python -m tools.punctuation_model inspect `
  --checkpoint .corpus-work/punctuation-model/best.pt
```

## 驗證

```powershell
python -m unittest discover -s tools/punctuation_model/tests -v
python -m py_compile tools/punctuation_model/*.py
```
