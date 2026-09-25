"""无显示器环境下的 OMR 导入回归测试。"""
import os
import subprocess
import sys
from pathlib import Path


def test_interaction_import_works_without_desktop_monitor():
    root = Path(__file__).resolve().parents[2]
    script = r'''
import sys
import types
screeninfo = types.ModuleType("screeninfo")
def get_monitors():
    raise RuntimeError("No enumerators available")
screeninfo.get_monitors = get_monitors
sys.modules["screeninfo"] = screeninfo
from src.utils.interaction import ImageMetrics
assert ImageMetrics.window_width == 1920
assert ImageMetrics.window_height == 1080
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "使用无头默认窗口尺寸" in (result.stdout + result.stderr)
