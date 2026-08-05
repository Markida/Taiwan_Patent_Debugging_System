import sys
from pathlib import Path


def get_app_icon_path():
    """Return the bundled cross-platform application icon."""

    return Path(__file__).resolve().parent / "resources" / "app_icon.png"


def get_app_base_dir():
    """
    取得 App 執行所在資料夾。

    原始碼執行時：
    回傳 main.py 所在資料夾。

    Nuitka 打包後：
    回傳 exe 所在資料夾。
    """

    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def get_app_install_dir():
    """Return the portable product root (the folder containing the launcher)."""

    base_dir = get_app_base_dir()
    if base_dir.name.lower() == "app":
        parent = base_dir.parent
        launcher_names = (
            "Saint-Island_Patent_MDS.exe",
            "Saint-IslandPatentOCR.exe",
            "SantoPatentOCR.exe",
        )
        if (parent / "runtime").is_dir() or any(
            (parent / name).is_file() for name in launcher_names
        ):
            return parent
    return base_dir


def get_output_base_dir():
    """
    輸出資料夾固定放到使用者 Documents。
    避免安裝後寫入 Program Files 或 .dist 資料夾造成權限問題。
    """

    output_dir = (
        Path.home()
        / "Documents"
        / "Saint-Island_Patent_MDS"
        / "outputs"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_easyocr_model_dir():
    """
    EasyOCR 模型固定放在 App 資料夾底下的 easyocr_models。
    """

    return get_app_base_dir() / "easyocr_models"


def get_models_dir():
    """
    外部模型資料夾。
    """

    return get_app_base_dir() / "models"
