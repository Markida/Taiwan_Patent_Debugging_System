# v2.2.07 圖題方向模型增補包

2026-09-21：依使用者要求，在 v2.2.07／v2.2.07c 更新包納入既有圖題方向試用模型，不重新訓練，不更換標號 OCR 模型。

## 產物

- `release/Saint-Island_Patent_MDS_v2.2.07_內部測試版本_20260921_含圖題方向模型.zip`
  - 94,808,926 bytes
  - SHA256: `b113f3ee88935f3c51eb1e9eed7dd639707e9a44327c052cbeec736beec8aecd`
- `release/Saint-Island_Patent_MDS_v2.2.07c_公司標準乾淨版_20260921_含圖題方向模型.zip`
  - 94,638,598 bytes
  - SHA256: `c128352b848e3d4e6662e1db5174c9a62335c7a37d5e5db233a960c4a0c09570`

## 模型與啟用方式

包含 `figure_heading_pilot_v1.onnx`、類別名稱及 experimental manifest。保留 `approved_for_production: false`；不偽裝為正式模型，不降低正式發布門檻。

打包需明確使用 `--include-figure-heading-trial`，先驗證模型／類別 SHA-256 才生成輸出。介面會偵測完整試用模型，使用者按「第一步自動旋轉」時才執行方向判斷。原圖不覆寫，無證據或模糊頁面保持原向，可手動旋轉。

現有資料未涵蓋英文字母／prime 圖號，負樣本有限，需人工核對方向。這次通過啟動與抽樣測試不代表已達全量正式模型發布門檻。

## 驗證

- 兩版 ZIP CRC、所有 manifest SHA-256、EXE 版本與版本隔離通過。
- 兩版各 8 個實際 ONNX 推論案例：同一正樣本的四方向及無圖題負樣本四方向。16/16 符合預期，來源檔案 SHA-256 不變，UI 已啟用試用模型。
- 兩版於隔離工作區執行實際 Install_Update.bat：安裝、正式 OCR 離線推論、EXE 啟動通過；既有 JSON 與 runtime 保留。
- Offline_Check 原本僅自動查找正式圖題模型，試用模型另由 `.qa/smoke_heading_package_2207.py` 直接從封裝內驗證。
- 封裝內功能回歸：內部版 527 項、標準版 417 項通過。

仍為保留既有 runtime 的公司端更新包；完整解壓後執行 Install_Update.bat，不可只複製 EXE。
