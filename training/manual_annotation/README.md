# 真實專利字元整頁標註

這套流程專門補救「定位器根本沒有框到字母、羅馬數字或 prime」的問題。裁切審核只能修改已存在的框，不能補回漏框，因此必須回到整張專利圖逐頁檢查。

新版資料規則：

- 只使用真實專利圖片，不使用舊版尺寸不符的合成頁。
- `VIII` 必須分成 `V`、`I`、`I`、`I` 四個框。
- `IV` 必須分成 `I`、`V` 兩個框。
- `7'` 必須分成 `7` 與 `prime` 兩個框。
- 每個字元框只包含一個字元；括號、箭頭、圖線不是字元，應刪除。
- 同一份 PDF 的頁面會固定放在同一個 train/val split，避免資料洩漏。
- 既有頁面會依裁切審核記錄連同所有框一起旋轉成正向，與實際 UI 旋轉後的輸入一致。

## 開始標註

在 PowerShell 中執行：

```powershell
.\annotate_patent_chars.bat
```

第一次啟動會以現有真實頁面與舊審核結果建立初始綠框。這些綠框只是加速起點，每一頁仍會標成「未完成」，因為還要補上舊定位器漏掉的字元。

介面操作：

- 選擇 class 後，用滑鼠拖曳字元外框。
- 數字鍵與英文字母鍵可快速切換 class；`'` 切換到 prime。
- 滑鼠滾輪縮放。
- 取消「繪框模式」後，可用滑鼠拖曳平移。
- `Delete` 刪除錯框，`Ctrl+S` 儲存。
- `Ctrl+Enter` 將本頁標記完成並前往下一頁。
- 「匯入測試圖片 / PDF」可以加入實際在 UI 辨識失敗的檔案；這些檔案最有訓練價值。

## 查看進度與資料缺口

```powershell
.\annotate_patent_chars.bat --stats
```

訓練前預設門檻：

- 所有頁面都必須標記完成。
- 每個 `0-9 / A-Z / prime` 至少 20 個真實框。
- 每個 class 的 validation split 至少 3 個真實框。
- 容易混淆且是本次重點的 `I / V / X / prime` 至少各 50 個，其中 validation 至少各 5 個。

未達門檻時，產生器會拒絕正式建置，避免再訓練出表面有 37 classes、實際沒有真實樣本的模型。

## 建立兩階段訓練資料

人工標註全部完成後才執行：

```powershell
conda run -n patent_pack_cpu python `
  training\manual_annotation\build_training_datasets.py
```

輸出包含：

1. `locator`：所有真實字元（包含 prime）都是同一 class，用於訓練整頁定位器。
2. `classifier`：每個裁切分成 `0-9 / A-Z / prime / background`，用於訓練第二階段分類器。

舊審核中被跳過的括號與圖線裁切會成為 `background`，讓分類器學會拒絕非字元。

## 建立整組標號定位資料

單獨定位細 `I` 與 prime 容易漏框，因此另提供整組標號資料產生器。它會把相鄰人工框合併，例如 `V + I + I` 變成 `VII`、`5 + 5 + prime` 變成 `55'`：

```powershell
conda run -n patent_pack_cpu python `
  training\manual_annotation\build_group_locator_dataset.py `
  --output training\manual_annotation\group_locator_dataset_v2
```

這個資料集只有一個 YOLO class：`patent_label`。字串內容不由 YOLO 分類，而是在完整標號框內交給 EasyOCR 判讀。
