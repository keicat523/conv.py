import json
from pathlib import Path
import config

# タイムアウトデータファイル設定
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = BASE_DIR / "data" / "limtime.json"
DEFAULT_TIMEOUT = 3.0
DEFAULT_MENU_TIMEOUT = float(getattr(config, "MENU_TIMEOUT", 15))


# 内部ファイルI/Oヘルパー
def _load() -> dict:
    if not DATA_PATH.exists():
        return {"seconds": DEFAULT_TIMEOUT, "menu_seconds": DEFAULT_MENU_TIMEOUT}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
            if "menu_seconds" not in data:
                data["menu_seconds"] = DEFAULT_MENU_TIMEOUT
            return data
    except Exception:
        return {"seconds": DEFAULT_TIMEOUT, "menu_seconds": DEFAULT_MENU_TIMEOUT}


def _save(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 一般コマンド用タイムアウト
def get_timeout_seconds() -> float:
    data = _load()
    try:
        return float(data.get("seconds", DEFAULT_TIMEOUT))
    except Exception:
        return DEFAULT_TIMEOUT


def set_timeout_seconds(seconds: float) -> float:
    data = _load()
    data["seconds"] = float(seconds)
    _save(data)
    return data["seconds"]


def reset_timeout_seconds() -> float:
    data = _load()
    data["seconds"] = DEFAULT_TIMEOUT
    _save(data)
    return data["seconds"]


# メニューコマンド用タイムアウト
def get_menu_timeout_seconds() -> float:
    data = _load()
    try:
        return float(data.get("menu_seconds", DEFAULT_MENU_TIMEOUT))
    except Exception:
        return DEFAULT_MENU_TIMEOUT


def set_menu_timeout_seconds(seconds: float) -> float:
    data = _load()
    data["menu_seconds"] = float(seconds)
    _save(data)
    return data["menu_seconds"]


def reset_menu_timeout_seconds() -> float:
    data = _load()
    data["menu_seconds"] = DEFAULT_MENU_TIMEOUT
    _save(data)
    return data["menu_seconds"]
