APP_STYLE = """
QStackedWidget {
    background: #eef3f8;
    color: #172033;
    font-family: "Microsoft JhengHei", "Noto Sans TC", Arial;
    font-size: 14px;
}

QWidget {
    color: #172033;
}

QLabel {
    color: #172033;
}

QLineEdit {
    color: #111827;
    background: #ffffff;
    selection-background-color: #1d4ed8;
    selection-color: #ffffff;
}

QTextEdit {
    color: #111827;
    background: #ffffff;
    selection-background-color: #1d4ed8;
    selection-color: #ffffff;
}

QPushButton {
    color: #111827;
    font-size: 12.5pt;
}

#HomePage {
    background: qlineargradient(
        x1: 0, y1: 0,
        x2: 1, y2: 1,
        stop: 0 #e8eef7,
        stop: 0.52 #f7f9fc,
        stop: 1 #d6e4f5
    );
}

#HomeTitle {
    color: #0b1f33;
    font-size: 58px;
    font-weight: 900;
    letter-spacing: 8px;
}

#HomeSubtitle {
    color: #17324d;
    font-size: 22px;
    font-weight: 700;
}

#HomeDescription {
    color: #24364a;
    font-size: 18px;
    font-weight: 600;
}

#HomeCard {
    background: rgba(255, 255, 255, 0.96);
    border: 1px solid #c8d6e6;
    border-radius: 22px;
    min-width: 420px;
    max-width: 520px;
}

#CardTitle {
    color: #0f2742;
    font-size: 22px;
    font-weight: 700;
}

#PrimaryHomeButton {
    background-color: #0f4c81;
    color: #ffffff;
    border: none;
    border-radius: 14px;
    font-size: 17pt;
    font-weight: 700;
    padding: 12px 28px;
}

#PrimaryHomeButton:hover {
    background-color: #1262a3;
    color: #ffffff;
}

#DisabledHomeButton {
    background-color: #d1d5db;
    color: #4b5563;
    border: none;
    border-radius: 12px;
    font-size: 14pt;
    padding: 10px 24px;
}

#PageTitle {
    color: #0f2742;
    font-size: 25px;
    font-weight: 800;
}

#Panel {
    background: #ffffff;
    border: 1px solid #d8e1ec;
    border-radius: 14px;
}

#PanelTitle {
    color: #0f2742;
    font-size: 17px;
    font-weight: 700;
}

#ImageScrollArea {
    background: #f8fafc;
    border: 1px solid #d8e1ec;
    border-radius: 12px;
}

#ImagePreview {
    background: #f8fafc;
    color: #334155;
    border: none;
    font-size: 16px;
}

#ReferenceText {
    background: #ffffff;
    color: #111827;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
    padding: 10px;
    font-family: "Consolas", "Microsoft JhengHei";
    font-size: 14px;
}

#ResultText {
    background: #ffffff;
    color: #111827;
    border: 1px solid #d7e0ea;
    border-radius: 12px;
    padding: 12px;
    font-family: "Consolas", "Microsoft JhengHei";
    font-size: 15px;
    line-height: 1.45;
}

#DocumentWhitelistPanel {
    background: #ffffff;
    color: #111111;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
}

#DocumentWhitelistPanel QLabel,
#DocumentWhitelistTitle {
    background: transparent;
    color: #111111;
}

#DocumentWhitelistTitle {
    font-size: 17px;
    font-weight: 700;
}

#DocumentWhitelistInput,
#DocumentWhitelistTable {
    background: #ffffff;
    color: #111111;
    border: 1px solid #cbd5e1;
    selection-background-color: #dbeafe;
    selection-color: #111111;
}

#DocumentWhitelistTable QHeaderView::section {
    background: #ffffff;
    color: #111111;
    border: none;
    border-right: 1px solid #cbd5e1;
    border-bottom: 1px solid #cbd5e1;
    padding: 5px;
    font-weight: 700;
}

#HiddenTitleLetter {
    color: #0b1f33;
    background: transparent;
    border: none;
    border-radius: 0;
    font-size: 58px;
    font-weight: 900;
    letter-spacing: 8px;
    padding: 0;
    margin: 0;
    min-width: 50px;
    max-width: 50px;
}

#HiddenTitleLetter:hover,
#HiddenTitleLetter:pressed {
    color: #0b1f33;
    background: transparent;
    border: none;
}

#FeatureNavigation {
    background: #dbe5f0;
    border: 1px solid #c4d2e1;
    border-radius: 9px;
}

#ArcadeNavigation {
    background: #dbe5f0;
    border: 1px solid #c4d2e1;
    border-radius: 10px;
}

#ArcadeNavigation #FeatureNavigationButton,
#ArcadeNavigation #ActiveFeatureNavigationButton {
    padding: 5px 9px;
    min-width: 48px;
}

#ArcadeTitle {
    color: #0f766e;
    font-size: 22px;
    font-weight: 900;
    letter-spacing: 4px;
    padding: 0 5px;
}

#ArcadeStatus {
    background: #e8f1fa;
    color: #17324d;
    border: 1px solid #b8c8d8;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 700;
}

#FeatureNavigationButton {
    background: #f8fafc;
    color: #18324b;
    border: 1px solid #b8c8d8;
    border-radius: 7px;
    font-weight: 700;
    padding: 5px 12px;
}

#FeatureNavigationButton:hover {
    background: #e7eef6;
    border-color: #7893ad;
}

#ActiveFeatureNavigationButton {
    background: #18324b;
    color: #ffffff;
    border: 1px solid #0f2742;
    border-radius: 7px;
    font-weight: 800;
    padding: 5px 12px;
}

#ChatUnreadBadge {
    background-color: #dc2626;
    color: #ffffff;
    border: 2px solid #ffffff;
    border-radius: 15px;
    font-size: 12pt;
    font-weight: 800;
    padding: 0;
}

#ChatUnreadBadge:hover {
    background-color: #b91c1c;
}

#ResultText QScrollBar:vertical {
    background: #eef2f7;
    border: none;
    border-radius: 8px;
    width: 16px;
    margin: 2px;
}

#ResultText QScrollBar::handle:vertical {
    background: #94a3b8;
    border-radius: 6px;
    min-height: 42px;
    margin: 2px;
}

#ResultText QScrollBar::handle:vertical:hover {
    background: #64748b;
}

#ResultText QScrollBar::add-line:vertical,
#ResultText QScrollBar::sub-line:vertical {
    height: 0;
}

#ResultText QScrollBar::add-page:vertical,
#ResultText QScrollBar::sub-page:vertical {
    background: transparent;
}

#ReviewTable {
    background: #ffffff;
    color: #111827;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    gridline-color: #dbe3ec;
    selection-background-color: #bfdbfe;
    selection-color: #111827;
    font-size: 12pt;
}

#TerminologyTable {
    background: #ffffff;
    alternate-background-color: #f8fafc;
    color: #111827;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    gridline-color: #dbe3ec;
    selection-background-color: #bfdbfe;
    selection-color: #111827;
    font-size: 12pt;
}

#ComparisonStatus {
    background: #e2e8f0;
    color: #1e293b;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 7px 10px;
    font-weight: 700;
}

#ComparisonText {
    background: #ffffff;
    color: #111827;
    border: 1px solid #cbd5e1;
    border-radius: 9px;
    padding: 10px;
    font-family: "Microsoft JhengHei";
    font-size: 14pt;
}

#ComparisonImage {
    background: #f8fafc;
    color: #64748b;
    border: 1px solid #cbd5e1;
    border-radius: 9px;
}

#ComparisonResult {
    background: #fff7ed;
    color: #172033;
    border: 2px solid #f59e0b;
    border-radius: 11px;
    padding: 14px 16px;
    font-size: 16pt;
    font-weight: 700;
}

#SnakeTitle {
    color: #0f766e;
    font-size: 28px;
    font-weight: 900;
    letter-spacing: 8px;
}

#SnakeScore {
    color: #0f2742;
    font-size: 20px;
    font-weight: 800;
}

#SnakeBoard {
    border: 2px solid #1e3a52;
    border-radius: 8px;
}

#SnakeBonus {
    background: #fef3c7;
    color: #92400e;
    border: 1px solid #f59e0b;
    border-radius: 8px;
    padding: 8px;
    font-weight: 800;
}

#SnakeLeaderboard {
    background: #0f1f31;
    alternate-background-color: #162a40;
    color: #ffffff;
    border: 1px solid #334e68;
    border-radius: 8px;
    gridline-color: #334e68;
    selection-background-color: #275b7a;
    selection-color: #ffffff;
    font-size: 14pt;
}

#SnakeLeaderboard QHeaderView::section {
    background: #18324b;
    color: #ffffff;
    border: none;
    border-right: 1px solid #3d5870;
    border-bottom: 1px solid #3d5870;
    padding: 8px 5px;
    font-size: 13pt;
    font-weight: 800;
}

#SnakeLeaderboard QTableCornerButton::section {
    background: #18324b;
    border: none;
}

#ChatMessageView {
    background: #eef3f8;
    color: #172033;
    border: 1px solid #b8c8d8;
    border-radius: 10px;
    padding: 10px;
    font-family: "Microsoft JhengHei";
    font-size: 12pt;
}

#ChatMessageView QScrollBar:vertical {
    background: #e2e8f0;
    border: none;
    border-radius: 8px;
    width: 18px;
    margin: 2px;
}

#ChatMessageView QScrollBar::handle:vertical {
    background: #64748b;
    border-radius: 7px;
    min-height: 44px;
    margin: 2px;
}

#ChatMessageView QScrollBar::handle:vertical:hover {
    background: #334155;
}

#ChatMessageView QScrollBar::add-line:vertical,
#ChatMessageView QScrollBar::sub-line:vertical {
    height: 0;
}

#ChatMessageInput {
    background: #ffffff;
    color: #111827;
    border: 1px solid #94a3b8;
    border-radius: 9px;
    padding: 8px;
    font-family: "Microsoft JhengHei";
    font-size: 13pt;
}

#ChatMessageInput:focus {
    border: 2px solid #0f4c81;
}

#ChatReplyPanel {
    background: #e6f2fb;
    color: #172033;
    border: 1px solid #91bad6;
    border-left: 5px solid #0f6fa8;
    border-radius: 8px;
}

#ChatConnected {
    background: #dcfce7;
    color: #166534;
    border: 1px solid #86efac;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 800;
}

#ChatDisconnected {
    background: #fee2e2;
    color: #991b1b;
    border: 1px solid #fca5a5;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 800;
}

#ChatAttachmentStatus {
    color: #475569;
    font-weight: 600;
}

#ChatAvatarPreview {
    background: #e2e8f0;
    color: #475569;
    border: 2px solid #94a3b8;
    border-radius: 27px;
    font-size: 9pt;
    font-weight: 800;
}

#ChatPresenceList {
    background: #f8fafc;
    color: #172033;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 5px;
    font-size: 12pt;
    font-weight: 700;
}

#ChatPresenceList::item {
    border-bottom: 1px solid #e2e8f0;
    padding: 7px 4px;
}

#ChatSideTabs::pane {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 9px;
    top: -1px;
}

#ChatSideTabs QTabBar::tab {
    background: #e7eef8;
    color: #334155;
    border: 1px solid #cbd5e1;
    padding: 8px 12px;
    min-width: 105px;
    font-weight: 700;
}

#ChatSideTabs QTabBar::tab:selected {
    background: #ffffff;
    color: #0f4c81;
    border-bottom-color: #ffffff;
}

#PongRoomList {
    background: #0f1f31;
    color: #ffffff;
    border: 1px solid #35536e;
    border-radius: 9px;
    padding: 6px;
    font-size: 13pt;
    font-weight: 800;
}

#PongRoomList::item {
    border-bottom: 1px solid #35536e;
    border-radius: 6px;
    padding: 11px 9px;
    margin: 2px;
}

#PongRoomList::item:hover,
#PongRoomList::item:selected {
    background: #1d5f8a;
    color: #ffffff;
}

#PongRoomList::item:disabled {
    background: #334155;
    color: #94a3b8;
}

#ReviewTable QHeaderView::section {
    background: #e7eef8;
    color: #0f2742;
    border: none;
    border-right: 1px solid #cbd5e1;
    border-bottom: 1px solid #cbd5e1;
    padding: 5px;
    font-size: 12pt;
    font-weight: 700;
}

#TerminologyTable QHeaderView::section {
    background: #e7eef8;
    color: #0f2742;
    border: none;
    border-right: 1px solid #cbd5e1;
    border-bottom: 1px solid #cbd5e1;
    padding: 5px;
    font-size: 12pt;
    font-weight: 700;
}

#TerminologyTable QTableCornerButton::section {
    background: #e7eef8;
    border: none;
    border-right: 1px solid #cbd5e1;
    border-bottom: 1px solid #cbd5e1;
}

#InputLine {
    background: #ffffff;
    color: #111827;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 9px;
}

#InputLine:focus {
    border: 1px solid #0f4c81;
}

#PrimaryButton {
    background-color: #0f4c81;
    color: #ffffff;
    border: none;
    border-radius: 9px;
    padding: 7px 16px;
    font-weight: 700;
}

#PrimaryButton:hover {
    background-color: #1262a3;
    color: #ffffff;
}

#PrimaryButton:disabled {
    background-color: #9ca3af;
    color: #f3f4f6;
}

#RecognitionStartButton {
    background-color: #dc2626;
    color: #ffffff;
    border: 2px solid #991b1b;
    border-radius: 9px;
    padding: 7px 18px;
    font-weight: 900;
}

#RecognitionStartButton:hover {
    background-color: #b91c1c;
    color: #ffffff;
}

#RecognitionStartButton:disabled {
    background-color: #9ca3af;
    color: #f3f4f6;
    border-color: #6b7280;
}

#GreenActionButton {
    background-color: #16a34a;
    color: #ffffff;
    border: 2px solid #15803d;
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 800;
}

#GreenActionButton:hover {
    background-color: #15803d;
}

#GreenActionButton:disabled {
    background-color: #bbf7d0;
    color: #64748b;
    border-color: #86efac;
}

#OrangeActionButton {
    background-color: #f59e0b;
    color: #422006;
    border: 2px solid #d97706;
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 800;
}

#OrangeActionButton:hover {
    background-color: #d97706;
    color: #ffffff;
}

#OrangeActionButton:disabled {
    background-color: #fde68a;
    color: #78716c;
    border-color: #fcd34d;
}

#LightBlueActionButton {
    background-color: #bae6fd;
    color: #0c4a6e;
    border: 1px solid #38bdf8;
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 800;
}

#LightBlueActionButton:hover {
    background-color: #7dd3fc;
}

#LightBlueActionButton:checked {
    background-color: #38bdf8;
    color: #082f49;
    border-color: #0284c7;
}

#LightBlueActionButton:disabled {
    background-color: #e0f2fe;
    color: #64748b;
    border-color: #bae6fd;
}

#SearchArrowButton {
    background-color: #dbeafe;
    color: #0f4c81;
    border: 1px solid #60a5fa;
    border-radius: 7px;
    padding: 0;
    font-size: 13pt;
    font-weight: 900;
}

#SearchArrowButton:hover {
    background-color: #bfdbfe;
    color: #0b3a63;
    border-color: #2563eb;
}

#SearchArrowButton:pressed {
    background-color: #2563eb;
    color: #ffffff;
}

#SearchArrowButton:disabled {
    background-color: #eef2f7;
    color: #64748b;
    border-color: #cbd5e1;
}

#SecondaryButton {
    background-color: #e7eef8;
    color: #0f2742;
    border: 1px solid #cbd5e1;
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 600;
}

#SecondaryButton:hover {
    background-color: #d6e4f5;
    color: #0f2742;
}

#SecondaryButton:checked {
    background-color: #2563eb;
    color: #ffffff;
    border-color: #1d4ed8;
}

#SecondaryButton:checked:hover {
    background-color: #1d4ed8;
    color: #ffffff;
}

#SecondaryButton:disabled {
    background-color: #e5e7eb;
    color: #6b7280;
}

#ToolButton {
    background-color: #eef2f7;
    color: #0f2742;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 6px 13px;
    font-weight: 600;
}

#ToolButton:hover {
    background-color: #dfe9f5;
    color: #0f2742;
}
"""
