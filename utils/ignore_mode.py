import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = BASE_DIR / "data" / "ignore_mode.json"

# 開発者用モードを実装するpy

# 現在開発用モードか取得ための関数
def _load() -> dict:
    if not DATA_PATH.exists():
        return {"enabled": False}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False}

# 開発者モードの変更を内部に保存するための関数
def _save(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 開発者モードか判定
def is_ignore_enabled() -> bool:
    return bool(_load().get("enabled", False))

# 開発者モードをオン
def set_ignore_enabled(enabled: bool) -> bool:
    data = _load()
    data["enabled"] = bool(enabled)
    _save(data)
    return data["enabled"]

# 開発者用とそうでない状態を切り替え
def toggle_ignore() -> bool:
    data = _load()
    data["enabled"] = not bool(data.get("enabled", False))
    _save(data)
    return data["enabled"]
