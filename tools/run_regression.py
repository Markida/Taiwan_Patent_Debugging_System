"""Run all source tests with isolated release QA storage."""
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".qa/regression"
OUTPUT.mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
profile = OUTPUT / "isolated_profile"
for folder in (profile, profile / "AppData/Local", profile / "AppData/Roaming", OUTPUT / "qt_settings"):
    folder.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1",
    "KMP_DUPLICATE_LIB_OK": "TRUE", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "WANDB_DISABLED": "true",
    "SAINT_ISLAND_CHAT_ROOM_ROOT": str(OUTPUT / "local_chat"),
    "SAINT_ISLAND_SNAKE_ROOT": str(OUTPUT / "local_snake"),
    "SAINT_ISLAND_TW_CN_TERMINOLOGY_PATH": str(OUTPUT / "missing_dictionary.txt"),
    "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "localhost,127.0.0.1,::1",
    "USERPROFILE": str(profile), "HOME": str(profile),
    "LOCALAPPDATA": str(profile / "AppData/Local"),
    "APPDATA": str(profile / "AppData/Roaming"),
})
sys.dont_write_bytecode = True
from PySide6.QtCore import QSettings
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(OUTPUT / "qt_settings"))
QSettings.setPath(QSettings.IniFormat, QSettings.SystemScope, str(OUTPUT / "qt_settings_system"))

started = time.monotonic()
suite = unittest.defaultTestLoader.discover("tests")
with (OUTPUT / "source_tests.log").open("w", encoding="utf-8") as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
summary = {
    "status": "PASS" if result.wasSuccessful() else "FAILED",
    "tests_run": result.testsRun, "seconds": round(time.monotonic()-started, 2),
    "failures": [{"test": test.id(), "details": details} for test, details in result.failures],
    "errors": [{"test": test.id(), "details": details} for test, details in result.errors],
    "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
}
(OUTPUT / "source_tests.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
raise SystemExit(not result.wasSuccessful())
