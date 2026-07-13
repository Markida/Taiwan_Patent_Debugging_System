# 空白功能模板

新增第二個或第三個功能時：

1. 複製 `features/demo_tool/`，並改成新功能名稱。
2. 複製 `ui/demo_tool_page.py`，修改頁面類別與顯示內容。
3. 在 `features/registry.py` 匯入新頁面，並新增一筆功能設定。
4. 啟動程式；首頁會依 registry 自動產生新功能按鈕。

功能的資料處理、服務與背景工作放在 `features/<功能名稱>/`；PySide6 頁面放在 `ui/`。
