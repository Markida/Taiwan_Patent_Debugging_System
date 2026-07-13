# YOLO 字元模型訓練流程

此流程建立 37 個獨立 class：

```text
0-9
A-Z
prime
```

`7'`、`8'`、`9'` 不會成為獨立 class，而是分別標註成數字與 `prime`；應用程式會在偵測後將相鄰字元組合回完整標號。

## 為何需要混合資料

`pdf-100` 是原始 PDF/JPG 素材，不是 YOLO 字元標註集。腳本會：

1. 每個案件優先讀取 `01.pdf`、`02.pdf` 等單頁檔；若單頁檔沒有文字層，才改用該案件一份完整圖式 PDF，避免重複頁面。
2. 從可選取文字的 PDF 自動取得真實字元座標。
3. 產生專利線稿風格合成資料，補足原始素材缺少的字母與 prime。
4. 以專案資料夾為單位切分真實 train/val，降低相似頁面洩漏到驗證集的風險。

舊的 `dataset/` 單類別標註不會被修改。

## 1. 建立資料集

在 `patent_ai` 環境執行：

```powershell
conda run -n patent_ai python training\char_yolo\prepare_dataset.py
```

輸出位於：

```text
training/char_yolo/dataset/
  data.yaml
  class_map.json
  manifest.json
  images/train/
  images/val/
  labels/train/
  labels/val/
  previews/train_samples.jpg
  previews/val_samples.jpg
```

腳本不會覆寫非空資料夾。要重新產生時，請指定新的輸出路徑：

```powershell
...\python.exe training\char_yolo\prepare_dataset.py --output D:\dataset_char_v2
```

## 2. 訓練

```powershell
conda run -n patent_ai python training\char_yolo\train_char_yolo.py
```

預設會使用既有專利 YOLO `best.pt` 作為遷移學習起點；若該檔不存在，則使用 `yolov8n.pt`。最佳模型會匯出到：

```text
models/patent_char_v1.pt
models/class_map.json
```

## 3. 應用程式辨識

在程式中選擇 `models/patent_char_v1.pt`。模型的 37 個 class 都能映射成字元，因此 `RECOGNITION_MODE = "auto"` 會自動啟用「YOLO 字元模型」模式，不再載入 EasyOCR。

可用固定 holdout 圖做完整驗證：

```powershell
conda run -n patent_ai python training\char_yolo\verify_char_model.py
```

驗證會要求應用程式完整輸出 `A`、`B`、`10A`、`7'`、`8'`、`9'`，並在 `outputs/char_model_verification/` 產生預測圖與 JSON 報告。

## 真實資料校正

合成資料可以建立第一版模型，但正式準確率仍應以真實的 `A-Z` 與 prime 標號頁面做人工標註、微調與獨立驗證。`manifest.json` 會列出原始 PDF 中完全沒有真實樣本的 class，不能只看合成驗證集的分數判斷上線品質。
