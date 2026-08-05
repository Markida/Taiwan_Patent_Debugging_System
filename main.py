import os

# 一定要放在 torch / cv2 / easyocr import 之前
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import sys
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from app.main_window import MainWindow
from app.paths import get_app_icon_path
from features.patent_ocr.offline_self_test import run_offline_self_test


def main():
    if "--offline-self-test" in sys.argv:
        argument_index = sys.argv.index("--offline-self-test")
        report_path = (
            sys.argv[argument_index + 1]
            if len(sys.argv) > argument_index + 1
            else None
        )
        return run_offline_self_test(report_path)

    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    icon_path = get_app_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    if "--gui-smoke-test" in sys.argv:
        QTimer.singleShot(1500, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
