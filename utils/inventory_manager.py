import json
import os
import config

# ==================================================
# パスの作成
# ==================================================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

ITEM_DATA_DIR = os.path.join(DATA_DIR, "item_data")
ITEM_DESC_DIR = os.path.join(DATA_DIR, "item_description")
USER_DATA_DIR = os.path.join(DATA_DIR, "user_items")

os.makedirs(USER_DATA_DIR, exist_ok=True)
os.makedirs(ITEM_DESC_DIR, exist_ok=True)

# ==================================================
# レアリティを取得し内部名と実行名の変換表を取得
# ==================================================
RARITY_MAP_PATH = os.path.join(ITEM_DATA_DIR, "rarity_map.json")
RARITY_EFF = config.RARITY_EFF

# リストの中身を文字列に変換
def _eff_names(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]

# 入力名に対して内部名に変換する表を作成
def _build_user_to_rarity() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, names in RARITY_EFF.items():
        for name in _eff_names(names):
            mapping[str(name).lower()] = key
    return mapping

# 上の内部名のやつ
USER_TO_RARITY = _build_user_to_rarity()


# レアリティ読み込み
def load_rarity_map():
    if not os.path.exists(RARITY_MAP_PATH):
        return {}
    with open(RARITY_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)

# 内部で使うレアリティを使う 
def normalize_rarity(rarity: str | None) -> str | None:
    if rarity is None:
        return None
    r = str(rarity).lower()
    return USER_TO_RARITY.get(r)

# rarityの表示名取得
def rarity_display_name(rarity_key: str) -> str:
    names = RARITY_EFF.get(rarity_key)
    if names is None:
        return str(rarity_key)
    return _eff_names(names)[0]

# rarityの重複回避
def get_user_rarity_inputs() -> list[str]:
    seen = set()
    result = []
    for names in RARITY_EFF.values():
        for name in _eff_names(names):
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result

# レアリティ読み込み
def get_all_rarities():
    return list(load_rarity_map().keys())


# ==================================================
# アイテムデータ部分
# ==================================================
# 取得用
def load_all_item_data():
    """
    {
      item_id: {
        name: str,
        rarity: str
      }
    }
    """
    items = {}
    rarity_map = load_rarity_map()
    # rarity_mapを見て読み込む、存在しなかったら無視
    for rarity, filename in rarity_map.items():
        path = os.path.join(ITEM_DATA_DIR, filename)
        if not os.path.exists(path):
            continue

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for item_id, item in data.items():
            items[item_id] = {
                "name": item.get("name", "不明"),
                "rarity": rarity
            }

    return items

# アイテム説明文読み込み
def load_all_item_descriptions():
    """
    {
      (rarity, item_id): description
    }
    """
    descs = {}
    rarity_map = load_rarity_map()
    # 上に同じ
    for rarity in rarity_map.keys():
        display_name = rarity_display_name(rarity)
        path = os.path.join(ITEM_DESC_DIR, f"{display_name}.json")
        if not os.path.exists(path):
            continue

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for item_id, desc in data.items():
            descs[(rarity, item_id)] = desc

    return descs

# 上で読み込んだ2つを統合したデータに
def load_all_item_data_with_desc():
    """
    {
      (rarity, item_id): {
        name: str,
        description: str
      }
    }
    """
    items = {}
    descs = load_all_item_descriptions()
    rarity_map = load_rarity_map()

    for rarity, filename in rarity_map.items():
        path = os.path.join(ITEM_DATA_DIR, filename)
        if not os.path.exists(path):
            continue

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for item_id, item in data.items():
            items[(rarity, item_id)] = {
                "name": item.get("name", "不明"),
                "description": descs.get((rarity, item_id), "説明なし")
            }

    return items


def item_exists(item_id: str, rarity: str | None = None) -> bool:
    """
    rarity_map.json を基準にアイテムの存在を確認する
    """
    rarity_map = load_rarity_map()
    item_id = str(item_id)

    # 入力にレアリティの指定がある場合
    if rarity:
        rarity = normalize_rarity(rarity)
        if rarity not in rarity_map:
            return False

        filename = rarity_map[rarity]
        path = os.path.join(ITEM_DATA_DIR, filename)

        if not os.path.exists(path):
            return False

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return item_id in data

    # 入力にレアリティの指定が無い場合
    for filename in rarity_map.values():
        path = os.path.join(ITEM_DATA_DIR, filename)
        if not os.path.exists(path):
            continue

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if item_id in data:
            return True

    return False


# ==================================================
# ユーザーデータ
# ==================================================
# ユーザーデータの保存場所取得
def _user_path(user_id):
    return os.path.join(USER_DATA_DIR, f"{user_id}.json")

# んでユーザーデータを読み込む
def load_user_data(user_id):
    rarity_map = load_rarity_map()
    path = _user_path(user_id)

    if not os.path.exists(path):
        return {r: {} for r in rarity_map.keys()}

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # 入力用データと内部データを変換
    for key in list(data.keys()):
        if key in rarity_map:
            continue
        mapped = USER_TO_RARITY.get(str(key).lower())
        if mapped and mapped in rarity_map:
            data.setdefault(mapped, {})
            for item_id, amount in data[key].items():
                data[mapped][item_id] = data[mapped].get(item_id, 0) + amount
            del data[key]

    for r in rarity_map.keys():
        data.setdefault(r, {})

    return data


def save_user_data(user_id, data):
    with open(_user_path(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==================================================
# admin用アイテム操作コマンド
# ==================================================

# アイテム個数加算
def add_item(user_id, item_id, rarity, amount=1):
    rarity_map = load_rarity_map()
    rarity = normalize_rarity(rarity)

    if rarity not in rarity_map:
        return False, "invalid_rarity"

    if amount <= 0:
        return False, "invalid_amount"

    if not item_exists(item_id, rarity):
        return False, "item_not_found"

    data = load_user_data(user_id)
    data[rarity][item_id] = data[rarity].get(item_id, 0) + amount
    save_user_data(user_id, data)

    return True, None

# アイテム個数減算
def remove_item(user_id, item_id, rarity, amount=1):
    rarity_map = load_rarity_map()
    rarity = normalize_rarity(rarity)

    if rarity not in rarity_map:
        return False, "invalid_rarity"

    if amount <= 0:
        return False, "invalid_amount"

    data = load_user_data(user_id)

    if item_id not in data[rarity]:
        return False, "not_owned"

    if data[rarity][item_id] < amount:
        return False, "not_enough"

    data[rarity][item_id] -= amount

    if data[rarity][item_id] <= 0:
        del data[rarity][item_id]

    save_user_data(user_id, data)
    return True, None


def get_inventory(user_id):
    return load_user_data(user_id)


# ==================================================
# アイテム表示用
# ==================================================
# IDから名前等を取得
def get_inventory_with_names(user_id):
    """
    {
      rarity: [
        (item_id, name, amount),
        ...
      ]
    }
    """
    inventory = load_user_data(user_id)
    items = load_all_item_data_with_desc()

    result = {}

    for rarity, data in inventory.items():
        result[rarity] = []
        for item_id, amount in data.items():
            info = items.get((rarity, item_id), {"name": "アイテム不明"})
            name = info.get("name", "アイテム不明")
            result[rarity].append((item_id, name, amount))

    return result

# インベントリ表示の基盤作成
def get_inventory_with_details(user_id):
    """
    {
      rarity: [
        (item_id, name, description, amount),
        ...
      ]
    }
    """
    inventory = load_user_data(user_id)
    items = load_all_item_data_with_desc()

    result = {}

    for rarity, data in inventory.items():
        result[rarity] = []
        for item_id, amount in data.items():
            info = items.get(
                (rarity, item_id),
                {"name": "不明なアイテム", "description": "説明なし"}
            )
            result[rarity].append(
                (item_id, info["name"], info["description"], amount)
            )

    return result


def create_item(rarity, name, description):
    rarity_map = load_rarity_map()
    rarity = normalize_rarity(rarity)

    if rarity not in rarity_map:
        return False, "invalid_rarity", None

    name = str(name).strip()
    description = str(description).strip()

    if not name:
        return False, "invalid_name", None
    if not description:
        return False, "invalid_description", None

    item_path = os.path.join(ITEM_DATA_DIR, rarity_map[rarity])
    item_data = {}
    if os.path.exists(item_path):
        with open(item_path, encoding="utf-8") as f:
            item_data = json.load(f)

    next_id = 1
    numeric_ids = []
    for key in item_data.keys():
        s = str(key)
        if s.isdigit():
            numeric_ids.append(int(s))
    if numeric_ids:
        next_id = max(numeric_ids) + 1

    item_id = str(next_id)
    item_data[item_id] = {"name": name}
    with open(item_path, "w", encoding="utf-8") as f:
        json.dump(item_data, f, ensure_ascii=False, indent=2)

    display_name = rarity_display_name(rarity)
    desc_path = os.path.join(ITEM_DESC_DIR, f"{display_name}.json")
    desc_data = {}
    if os.path.exists(desc_path):
        with open(desc_path, encoding="utf-8") as f:
            desc_data = json.load(f)
    desc_data[item_id] = description
    with open(desc_path, "w", encoding="utf-8") as f:
        json.dump(desc_data, f, ensure_ascii=False, indent=2)

    return True, None, item_id
