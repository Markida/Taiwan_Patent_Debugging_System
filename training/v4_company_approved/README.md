# 公司核准案件 v4 資料前處理與人工標註

狀態：資料前處理、兩批人工標註整併、v4 訓練、驗證與信心校準均已完成。

## PDF 與圖片前處理結果

- 官方圖式 PDF：299 件、1,355 頁。
- 轉圖格式：300 DPI、2481×3508、1-bit 黑白 PNG。
- 未旋轉、未翻轉、未做幾何增強。
- 頁首 7% 與頁尾 6% 以白色遮罩清除公報頁碼。
- 依 PDF 文字座標清除 3,212 個公報文字區域，共 26,913 個文字；嵌入的圖式與標號影像不受影響。
- 原本 11 個文字與圖式共頁的頁面已安全清理，不需要人工裁頁。
- 3 個頁面清理後為全白，確認沒有實際圖式，已排除。
- 可用圖式頁：1,352；train 1,177、未標註 holdout 175。
- train 與 holdout 以公告號整案切分，案件交集為 0。

圖片資料與完整 manifest：

- `training/data_collection/saint_island_latest_300_20260727/preprocessed_corpus`

## 保守預標註結果

- 已處理 train 圖式頁：1,177。
- 群組定位器檢查的標號群組：24,838。
- 新增通過 v1/v2 保守共識的標號裁切：2,192。
- 加上原有人工基礎資料後：train 2,859 張、val 18 張；圖片與標籤數完全一致。
- 175 張新 holdout 沒有人工框，只供日後端到端質化測試，不會冒充 mAP ground truth。
- 所有模型分歧、低信心與拒絕原因都保存在 `work/pseudo_audit.jsonl`。

## 為什麼需要人工標註

模型實際產生 11,990 個需要複核的群組，因此人工標註確實有必要。為控制工作量，只挑出最高價值的 400 個真實案例，沒有使用合成圖或為了湊數加入正常案例：

- 數字 `1` 漏辨識：120。
- `0`、小圓圈與 `J` 高風險案例：80。
- `I / V / X / prime`：80。
- 英文字母：80。
- 其他漏字或模型衝突：40。

## 開始標註

雙擊專案根目錄的：

```text
Annotate_V4_Company_Approved.bat
```

操作原則：

1. 綠框是模型建議，不代表一定正確；必須補上漏框並刪掉錯框。
2. `211` 要分成 `2 + 1 + 1` 三個字元框。
3. `VIII` 分成 `V + I + I + I`；`IV` 分成 `I + V`。
4. `7'` 分成 `7 + prime`，prime 不與數字共用一框。
5. 小圓圈不是 `0` 時刪除框；非字元群組可保留為零框的 hard negative。
6. 確認整張裁切圖後勾選「本頁已逐一檢查完成」，或按 `Ctrl+Enter` 儲存並前往下一張。

可雙擊 `Check_V4_Company_Approved_Annotations.bat` 檢查完成數、資料有效性及更新抽樣圖。

## 人工標註整併結果

人工標註已於本次完成並成功匯入：400/400，剩餘 0，無效框 0，包含 1 個零框 hard negative。匯入後的最終資料集為 train 3,259 張、val 18 張，圖片與標籤數一致。

另從上一輪 v3 人工標註區直接讀取 400/400 張已完成的真實裁切；9 張因影像 SHA-256 完全相同而排除，實際新增 391 張（含 4 張空白 hard negative）。沒有讀取或複製舊 v3 pseudo-labelled dataset。

最終 v4 資料集：

- train 3,650 張、val 18 張。
- train/val 的圖片與標籤數完全一致。
- class ID、框座標與 63 類順序均通過驗證。

日後若再次修改本次 v4 標註，可重新雙擊：

```text
Import_V4_Company_Approved.bat
```

上一輪人工標註的獨立匯入程式：

```text
training/v4_company_approved/import_prior_manual_reviews.py
```

## v4 訓練與驗證結果

- 起始權重：`models/patent_char_v3_consensus.pt`（63 類）。
- 設定：imgsz 1536、batch 4、最多 15 epochs、patience 5、無旋轉與翻轉。
- 第 8 輪由 early stopping 正常結束，沒有硬跑剩餘 7 輪。
- 綜合最佳版：precision 0.899、recall 0.934、mAP50 0.980、mAP50-95 0.858。
- 高召回版（第 8 輪）：precision 0.840、recall 0.957、mAP50 0.978、mAP50-95 0.871。
- 高召回版的數字 `1` recall 為 0.983，與 v3 相同；綜合最佳版只有 0.916，因此不可直接用綜合最佳版取代 v3。
- 在 18 張人工驗證頁上，高召回版的 F1 最佳推論門檻為 0.30；若優先避免漏字，0.05 可取得 recall 0.974，低信心結果交由 GUI 紅框人工檢查。

模型：

- `models/patent_char_v4_company_approved.pt`：綜合最佳版。
- `models/patent_char_v4_company_approved_recall.pt`：建議先做公司實圖 A/B 測試的高召回版。
- `models/patent_char_v4_company_approved_recall.onnx`：高召回版的公司離線 EXE 相容格式。
- 兩個 ONNX 均附 63 類 `.names.json` sidecar；高召回版已通過應用程式 OpenCV-DNN 實際推論。
- 現有 v3 未被覆蓋，GUI 預設模型也尚未切換。

完整報告：

- `preflight_report.json`
- `manual_review/selection_report.json`
- `manual_review_qa.json`
- `manual_review_contact_sheet.png`
- `../char_yolo/dataset_v4_company_approved/build_report.json`
- `../char_yolo/dataset_v4_company_approved/prior_manual_import.json`
- `../../models/patent_char_v4_company_approved_metrics.json`
- `../../models/patent_char_v4_comparison.json`
- `../../models/patent_char_v4_checkpoint_comparison.json`
- `../../models/patent_char_v4_recall_confidence_calibration.json`
