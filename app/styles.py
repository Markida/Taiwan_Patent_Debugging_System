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
    font-size: 20px;
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
    font-size: 16px;
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