import json
import discord

# helpコマンドの中身を取得する関数
def load_embed_from_json(path: str) -> discord.Embed:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 中身の要素をEmbedに組み込み
    embed = discord.Embed(
        title=data.get("title", ""),
        description=data.get("description", ""),
        color=discord.Color.blurple()
    )
    # 説明文全部読み込む
    for field in data.get("fields", []):
        label = field.get("label", "")
        value = field.get("value", "")

        # 配列なら複数行にする
        if isinstance(value, list):
            value = "\n\n".join(f"`{v}`" for v in value)
        else:
            value = f"```{value}```"

        embed.add_field(
            name=label,
            value=value,
            inline=False
        )

    return embed
