# 第二階段：黃金資料微調完整標號定位器 v2

本階段已取消額外人工審核、人工修框與 pseudo label 流程。訓練只使用先前完成的
172 張黃金頁面，並先依專利案號隔離成兩部分：

- train：138 頁、32 件專利、2,605 個完整標號群組。
- sealed holdout：34 頁、8 件專利、502 個完整標號群組。

同一件專利的頁面不會跨越 train 與 holdout；圖片 SHA-256 也沒有重複。原本的
172 頁總集合在拆分後不再用作 v2 的獨立驗證，所有 v1／v2 比較只能使用
`sealed_holdout_gold_v2`。

## 資料與用途

- `supervised_locator_v2_dataset`：138 頁訓練資料與 34 頁驗證資料；所有框都來自
  已完成的黃金標註，沒有模型自動標籤。
- `sealed_holdout_gold_v2`：供門檻掃描與端到端 OCR 評估的封存驗證視圖。
- `rare_character_holdout_v2`：只從 sealed holdout 裁出的 68 個英文、小寫、prime
  與多字元羅馬數字案例；禁止回流訓練。

不存在第二階段人工標註批次檔或人工修訂資料夾。若未來取得準確率更高的模型，
要重新導入人工主動學習時，必須建立全新版本，不能改動這份 sealed holdout。

## 重建資料

```powershell
..\SantoOCR\runtime\python.exe -m training.second_stage.build_supervised_locator_v2_dataset --force
..\SantoOCR\runtime\python.exe -m training.second_stage.build_sealed_holdout_view --force
..\SantoOCR\runtime\python.exe -m training.second_stage.build_rare_challenge --force
```

## 訓練

雙擊專案根目錄的 `Train_Group_Locator_V2.bat`，或使用 GPU 環境執行：

```powershell
$env:YOLO_CONFIG_DIR = (Join-Path (Get-Location).Path '.ultralytics_ai')
$env:PYTHONPATH = (Get-Location).Path
& "$env:USERPROFILE\anaconda3\envs\patent_ai\python.exe" `
  -m training.second_stage.train_group_locator_v2 --device 0
```

模型由正式 `models/patent_label_group_v1.pt` 接續微調，預設 30 epochs、無旋轉增強；
輸出為 `models/patent_label_group_v2_gold_ft.pt` 與 ONNX。是否採用新模型只看 sealed
holdout 的端到端 OCR 指標，不以訓練集或人工挑選案例決定。

## 本次封存結果

v2 在 confidence 0.10、NMS 0.30 的 34 頁 holdout：

- 完整標號定位：496/502，recall 98.80%。
- 端到端文字正確：466/502，recall 92.83%。
- v1 同一 holdout：419/502，recall 83.47%。

2x2 切片只多補回 1 個群組，卻大幅增加誤框，因此不採用。

## 完整標號文字辨識實驗

`ocr_recognizer_v2_dataset` 由同一批黃金框自動裁切，不使用 pseudo label，也不要求
新增人工審核。為避免封存集洩漏，訓練／內部驗證／sealed holdout 依專利案號分成：

- train：2,106 張裁切圖、27 件專利、107 頁。
- internal val：499 張裁切圖、5 件專利、31 頁。
- sealed holdout：502 張裁切圖、8 件專利、32 張含標號頁面。

曾以原始 `english_g2.pth` 為起點測試全字元及純數字 prediction head 微調。雖然
internal val 最高可達 97.19%，sealed holdout 最多只增加 1 筆，而且英文字母、
小寫或 prime 反而下降；純數字版本的總正確數也下降。因此這些實驗模型均不進入
正式版，正式 OCR 仍使用原始 EasyOCR 權重。

正式版改採「已擷取符號清單的保守校正」：只在清單內存在唯一候選時，修正同長度
的已知單字形混淆（例如 `Ll → L1`、`WI → W1`），不自動增刪任何字元或數字。
在同一 sealed holdout 上，端到端正確數由 466/502 提升到 473/502；逐筆稽核為
7 筆改善、0 筆誤改。模型原始輸出仍會保留，且不覆蓋使用者手動修改。
