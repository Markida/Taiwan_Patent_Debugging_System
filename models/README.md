# 模型說明

## 目前版本（2026-09-24）

- v2.2.07 預設使用 `patent_label_group_v2_gold_ft.onnx`，找出完整標號後交由離線 `english_g2.pth` 辨識。
- 圖題方向模型 `figure_heading_pilot_v1.onnx` 是獨立試用模型；需配套 names 與 experimental manifest 通過雜湊檢查，才可由第一步自動旋轉啟用。不是正式發布模型。
- 模型權重、個別案件評估及資料集專用 manifest 不放入公開 Git；公司端模型由更新包另外提供。
- 以下 v1／v2 數據與建議為歷史紀錄，當前預設與門檻以 `app/config.py`、`ui/recognition_page.py` 為準。

```text
best.pt            原始單一 class 字元定位器；定位後交給 EasyOCR 辨識字元。
best_v2.pt         使用人工清理後的真實專利圖資料微調；保留作為舊版逐字元流程。
patent_label_group_v1.pt  真實人工框訓練的整組標號定位器；目前建議優先測試。
patent_char_v1.pt  37-class 實驗模型（0-9、A-Z、prime）；暫不建議用於真實頁面。
```

## 建議設定

- 在辨識頁優先選擇 `patent_label_group_v1.pt`。
- 保持 `IMG_SIZE = 1536`、`YOLO_CONF = 0.25`、`YOLO_IOU = 0.40`、`MAX_X_GAP = 15`。
- 新模型直接定位完整標號，例如 `VII`、`10A`、`55'`，再由 EasyOCR 一次讀取整組。
- UI 會依模型 class `patent_label` 自動切換整組 OCR，不需要手動切模式。
- `best_v2.pt` 保留作為相容與回退模型。

## `patent_label_group_v1.pt` 驗證結果

資料來自 92 張已人工完成的真實專利頁，旋轉方向已依 UI 使用方式正規化，沒有使用舊版尺寸不符的合成頁。固定使用 UI 門檻 `imgsz=1536`、`conf=0.25`、`iou=0.40`：

| 項目 | Precision | Recall | F1 |
|---|---:|---:|---:|
| 完整標號框 IoU 0.30 | 0.9626 | 0.9759 | 0.9692 |
| 完整標號框 IoU 0.50 | 0.9388 | 0.9517 | 0.9452 |

18 張獨立驗證頁的端到端結果：

- 框到後文字正確率：93.57%（262/280）。
- 端到端正確召回：90.34%（262/290）。
- Roman 群組：6/6 正確。
- prime 群組：1/1 正確，實際輸出為 `55'`。
- 真實訓練頁中的 `IV` 4/4、`VII` 4/4；資料中沒有真正的 `VIII`，目前只有筆畫計數邏輯支援，仍需用真實 `VIII` 頁面確認。

完整報告見 `group_locator_v1_fixed.json`、`group_locator_v1_end_to_end.json` 與 `group_locator_v1_train_targets.json`。

## `best_v2.pt` 驗證結果

在人工清理後的驗證集（18 張圖、562 個字元框）以 `imgsz=1536` 測得：

| 模型 | Precision | Recall | F1 | mAP50 | mAP50-95 |
|---|---:|---:|---:|---:|---:|
| `best.pt` | 0.929981 | 0.968964 | 0.949073 | 0.985662 | 0.839291 |
| `best_v2.pt` | 0.939644 | 0.969558 | 0.954367 | 0.987788 | 0.850892 |

完整機器可讀結果見 `best_v2_metrics.json`。

## 37-class 實驗模型限制

`patent_char_v1.pt` 的 class 順序記錄在 `class_map.json`：

```text
0-9, A-Z, prime
```

它的合成驗證成績不能代表真實專利頁面的效果。部分字元與 prime 缺少可用的真實樣本，因此目前不應取代整組標號流程。
