# Patent character model v3 training

這個資料夾已把第三版模型的準備、資料建置、訓練、續訓與驗證拆開。正式訓練不會因為執行環境檢查或資料整理而意外開始。

## 為什麼不是直接拿 3,909 張訓練

TIPO 素材是完整圖式頁，沒有字元框。v3 的目標字彙為 `0-9 + A-Z + prime + a-z` 共 63 類；為保持相容，原本 37 類的 ID 0-36 完全不變，小寫 a-z 追加為 ID 37-62。YOLO 不能從「只有圖片、沒有框」的資料直接學會這些類別，因此先沿用目前實際 GUI 的兩階段架構：

1. `patent_label_group_v1.pt` 從完整頁找出標號群組。
2. `patent_char_v1.pt` 與 `patent_char_v2_domain.pt` 同時判讀裁切圖。
3. 同類且位置重疊的結果採用低門檻共識；單一模型結果須通過高門檻。
4. v1 單獨判成 `0` 或 `J` 永不自動採用，避免小圓圈與線條污染資料。
5. 只要裁切圖仍有未解決候選，整張裁切圖都不會進訓練集，避免漏標字元被當成背景。
6. 原 v2 的 18 張人工真實 val 完全保留，作為 v1/v2/v3 可直接比較的 mAP 驗證集。

TIPO 的 500 張 val 沒有框，因此只保留為「未接觸的端到端質化測試」，不能宣稱是 mAP 驗證資料。

## 明天建議順序

1. 雙擊 `Check_V3_Training.bat`。這會做 CUDA 與三模型 smoke test，不會訓練。
2. 雙擊 `Prepare_V3_Dataset.bat`。中斷後再次執行會從 `processed_pages.txt` 接續。
3. `Prepare_V3_Dataset.bat` 會另外挑出約 400 張最值得人工確認的群組小圖。雙擊 `Annotate_V3_Manual.bat`，修正預先框好的字元、補上漏框；圓圈不是 `0` 時刪掉框並把該圖標為完成，便會成為真正的負樣本。
4. 雙擊 `Import_V3_Manual.bat`，將已完成項目合併進 train。18 張人工 val 不會被更動。
5. 查看 `training/char_yolo/dataset_v3_consensus/build_report.json`，並抽查 `training/v3/work/review` 的紅框圖。
6. 雙擊 `Train_V3.bat`，確認摘要後輸入大寫 `TRAIN` 才會正式開始。
7. 若電腦重開或訓練中止，雙擊 `Resume_V3.bat`，它會選最新的 `last.pt`。

正式輸出：

- `models/patent_char_v3_consensus.pt`
- `models/patent_char_v3_consensus.onnx`
- `models/patent_char_v3_consensus_metrics.json`
- `models/patent_char_v3_comparison.json`

## 模型策略

第一個正式基線從 `patent_char_v2_domain.pt` 轉移學習。這能保留既有專利字型、定位骨幹與原 37 類知識；輸出層擴成 63 類後，小寫主要依靠本次人工標註學習。設定為 1536 px、最多 15 epochs、AdamW、無旋轉/翻轉、輕微平移縮放與低比例 mosaic；每 5 epochs 留一個 checkpoint，並可由 `last.pt` 接續中斷的訓練。

若 v3 基線證明資料品質良好但容量不足，再用同一資料測試較大的 YOLO small 模型。兩者必須用相同 18 張人工 val 比較，不能只看訓練 loss。

較大型候選權重已預存為 `models/pretrained/yolov8s.pt`。它會重建 80→63 類 detection head，因此不應取代第一個 v2 續訓基線；需要做第二實驗時使用：

```powershell
.\.venv_train_v3\Scripts\python.exe -m training.v3.train_v3 --start --model models\pretrained\yolov8s.pt --allow-head-reset --batch 2 --name patent_char_v3_yolov8s --export-model models\patent_char_v3_yolov8s.pt
```

## 顏色與稽核

- 綠框：已接受的候選。
- 紅框：任一 teacher 的未解決候選，只供複核，預設不進訓練。
- `training/v3/work/pseudo_audit.jsonl`：每個群組的模型信心度、採用/拒絕原因與來源座標。

所有自動標註都有來源紀錄；不會把 500 張 TIPO holdout 混入 train，也不會加入旋轉增強。
