import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = BASE_DIR / "data" / "user_memo.json"


def _load() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_memos(user_id: int) -> list[str]:
    data = _load()
    memos = data.get(str(user_id), [])
    if not isinstance(memos, list):
        return []
    return memos


def add_memo(user_id: int, text: str) -> int:
    data = _load()
    memos = data.get(str(user_id), [])
    if not isinstance(memos, list):
        memos = []
    memos.append(str(text))
    data[str(user_id)] = memos
    _save(data)
    return len(memos)


def remove_memo(user_id: int, memo_id: int) -> bool:
    data = _load()
    memos = data.get(str(user_id), [])
    if not isinstance(memos, list):
        return False
    idx = memo_id - 1
    if idx < 0 or idx >= len(memos):
        return False
    memos.pop(idx)
    data[str(user_id)] = memos
    _save(data)
    return True
