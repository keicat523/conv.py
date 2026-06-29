from dotenv import load_dotenv
load_dotenv()

import os

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')

# メニュー系コマンドの制限秒数
MENU_TIMEOUT = 15

# メニュータイムアウトで管理するコマンド名
MENU_TIMEOUT_EXEMPT_COMMANDS = [
    "help",
    "inventory",
]

# 実行時間制限の対象外コマンド名
TIMEOUT_EXEMPT_COMMANDS = [
    "graph",
    "info",
    "latex",
    "tex",
    "execute",
    "exe",
    "quiz",
    "web",
    "open",
    *MENU_TIMEOUT_EXEMPT_COMMANDS,
]

RARITY_EMOJI = {
    "n": "⚪",
    "r": "🔵",
    "e": "🟣",
    "l": "🟡"
}

RARITY_EFF = {
    "n": ["normal"],
    "r": ["rare"],
    "e": ["epic"],
    "l": ["legendary", "leg"]
}

PREV_EMOJI = "◀️"
NEXT_EMOJI = "▶️"
FIRST_EMOJI = "⏮️"
LAST_EMOJI = "⏭️"
