import json
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "user_data")
DEFAULT_PREFIX = "!"

os.makedirs(DATA_DIR, exist_ok=True)


def _path(user_id: int) -> str:
    return os.path.join(DATA_DIR, f"{user_id}.json")


def has_custom_prefix(user_id: int) -> bool:
    return os.path.exists(_path(user_id))


def load_prefix(user_id: int) -> str:
    path = _path(user_id)
    if not os.path.exists(path):
        return DEFAULT_PREFIX

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("prefix", DEFAULT_PREFIX)
    except Exception:
        return DEFAULT_PREFIX


def save_prefix(user_id: int, prefix: str):
    path = _path(user_id)
    payload = {"user_id": user_id, "prefix": prefix}

    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_prefix_",
        suffix=".json",
        dir=DATA_DIR
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
