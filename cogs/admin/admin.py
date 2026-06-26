import ast
import asyncio
import contextlib
import io
import os
import re
import sys
import textwrap
import traceback

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from discord.ext import commands

from admin_ids import ADMIN_IDS, save_admin_ids
from utils.timeout_manager import get_menu_timeout_seconds


MENTION_RE = re.compile(r"<@!?(\d+)>")
CODE_BLOCK_RE = re.compile(r"^```(?:py|python)?\s*\n(?P<code>.*?)(?:\n```)?\s*$", re.DOTALL)
EXECUTE_TIMEOUT_SECONDS = 180
EXECUTE_MAX_OUTPUT = 1800
EXECUTE_STOP_WORDS = {"stop", "停止", "とめて", "止めて"}
SAFE_EXEC_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "Exception": Exception,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "RuntimeError": RuntimeError,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "ValueError": ValueError,
    "zip": zip,
}
FORBIDDEN_EXEC_NAMES = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "exit",
    "input",
    "open",
    "quit",
}
FORBIDDEN_EXEC_ATTRS = {
    "_exit",
    "chmod",
    "chown",
    "close",
    "copy",
    "copyfile",
    "copytree",
    "link",
    "mkdir",
    "makedirs",
    "move",
    "remove",
    "removedirs",
    "rename",
    "replace",
    "rmdir",
    "rmtree",
    "stop",
    "symlink",
    "truncate",
    "unlink",
    "write",
    "write_bytes",
    "write_text",
    "writelines",
}
FORBIDDEN_EXEC_IMPORTS = {
    "builtins",
    "importlib",
    "os",
    "pathlib",
    "shutil",
    "subprocess",
    "sys",
}


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="admin")
    async def admin_cmd(
        self,
        ctx: commands.Context,
        action: str | None = None,
        target: str | None = None,
    ) -> None:
        if action is None:
            await ctx.reply("使用方法: c!admin <add/remove/list> [id or mention]")
            return

        action = action.lower()
        if action == "list":
            await self._admin_list(ctx)
            return
        if action == "add":
            await self._admin_add(ctx, target)
            return
        if action == "remove":
            await self._admin_remove(ctx, target)
            return

        await ctx.reply("使用方法: c!admin <add/remove/list> [id or mention]")

    @commands.command(name="execute", aliases=["exe"])
    async def execute_cmd(self, ctx: commands.Context, *, code: str | None = None) -> None:
        if not code:
            await ctx.reply("使用方法: c!exe ```py\nprint('hello')\n```")
            return

        source = self._extract_code(code)
        if not source.strip():
            await ctx.reply("実行するコードが空です")
            return

        blocked_reason = self._find_unsafe_execute_code(source)
        if blocked_reason is not None:
            await ctx.reply(f"安全のため実行を止めました: {blocked_reason}")
            return

        stdout = io.StringIO()
        stderr = io.StringIO()
        env = {
            "__builtins__": SAFE_EXEC_BUILTINS,
            "__name__": "__admin_execute__",
            "asyncio": asyncio,
            "bot": self.bot,
            "ctx": ctx,
        }

        try:
            wrapped = "async def __admin_execute_fn__():\n" + textwrap.indent(source, "    ")
            exec(wrapped, env, env)
            execute_task = asyncio.create_task(
                self._run_execute_function(env["__admin_execute_fn__"], stdout, stderr)
            )
            stop_task = asyncio.create_task(self._wait_for_execute_stop(ctx))
            done, pending = await asyncio.wait(
                {execute_task, stop_task},
                timeout=EXECUTE_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                execute_task.cancel()
                stop_task.cancel()
                with contextlib.suppress(BaseException):
                    await execute_task
                with contextlib.suppress(BaseException):
                    await stop_task
                await ctx.reply(f"実行時間制限({EXECUTE_TIMEOUT_SECONDS // 60}分)を超過しました")
                return

            if stop_task in done:
                execute_task.cancel()
                with contextlib.suppress(BaseException):
                    await execute_task
                await ctx.reply("実行を停止しました")
                return

            stop_task.cancel()
            with contextlib.suppress(BaseException):
                await stop_task
            for task in pending:
                task.cancel()

            result = execute_task.result()
            output = stdout.getvalue() + stderr.getvalue()
            if result is not None:
                output += repr(result)
        except BaseException as exc:
            output = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            await self._reply_execute_output(ctx, "エラーが発生しました", output)
            return

        await self._reply_execute_output(ctx, "実行結果", output or "実行完了 (出力なし)")

    async def _admin_list(self, ctx: commands.Context) -> None:
        await ctx.reply(self._build_admin_list_text(ctx))

    async def _admin_add(self, ctx: commands.Context, target: str | None) -> None:
        user_id = self._parse_user_id(target)
        if user_id is None:
            await ctx.reply("使用方法: c!admin add <id or メンション>")
            return
        if user_id in ADMIN_IDS:
            await ctx.reply("そのIDはすでに管理者です")
            return

        ADMIN_IDS.add(user_id)
        save_admin_ids()
        await ctx.reply(f"`{user_id}` を管理者に追加しました")

    async def _admin_remove(self, ctx: commands.Context, target: str | None) -> None:
        user_id = self._parse_user_id(target)
        if user_id is None:
            admin_ids = sorted(ADMIN_IDS)
            if not admin_ids:
                await ctx.reply("管理者は登録されていません")
                return

            prompt = await ctx.reply(
                self._build_admin_list_text(ctx) + "\n\n削除する番号またはIDを送信してください"
            )
            reply = await self._wait_for_same_user_message(ctx)
            if reply is None:
                try:
                    await prompt.add_reaction("❌")
                except Exception:
                    pass
                return

            content = reply.content.strip()
            if content.isdigit():
                selected = int(content)
                if 1 <= selected <= len(admin_ids):
                    user_id = admin_ids[selected - 1]
                else:
                    user_id = int(content)
            else:
                user_id = self._parse_user_id(content)

        if user_id is None:
            await ctx.reply("削除するIDが不正です")
            return
        if user_id not in ADMIN_IDS:
            await ctx.reply("そのIDは管理者一覧にありません")
            return

        ADMIN_IDS.remove(user_id)
        save_admin_ids()
        await ctx.reply(f"`{user_id}` を管理者一覧から削除しました")

    def _parse_user_id(self, raw: str | None) -> int | None:
        if raw is None:
            return None

        text = raw.strip()
        if not text:
            return None

        mention = MENTION_RE.fullmatch(text)
        if mention is not None:
            return int(mention.group(1))

        if text.isdigit():
            return int(text)

        return None

    def _extract_code(self, raw: str) -> str:
        text = raw.strip()
        match = CODE_BLOCK_RE.match(text)
        if match is not None:
            return match.group("code")
        return text

    def _find_unsafe_execute_code(self, source: str) -> str | None:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return f"構文エラー: {exc.msg} (line {exc.lineno})"

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for name in names:
                    root_name = name.split(".", 1)[0]
                    if root_name in FORBIDDEN_EXEC_IMPORTS:
                        return f"`{root_name}` の import は許可されていません"

            if isinstance(node, ast.Name) and node.id in FORBIDDEN_EXEC_NAMES:
                return f"`{node.id}` は許可されていません"

            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__"):
                    return "特殊属性へのアクセスは許可されていません"
                if node.attr in FORBIDDEN_EXEC_ATTRS:
                    return f"`.{node.attr}` は許可されていません"

            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
                return "`while True` は許可されていません"

        return None

    async def _run_execute_function(self, func, stdout: io.StringIO, stderr: io.StringIO):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return await func()

    async def _wait_for_execute_stop(self, ctx: commands.Context):
        def check(message) -> bool:
            return (
                message.author == ctx.author
                and message.channel == ctx.channel
                and message.content.strip().lower() in EXECUTE_STOP_WORDS
            )

        return await self.bot.wait_for("message", check=check)

    async def _reply_execute_output(self, ctx: commands.Context, title: str, output: str) -> None:
        if len(output) > EXECUTE_MAX_OUTPUT:
            output = output[-EXECUTE_MAX_OUTPUT:]
            output = "...(出力が長いため末尾のみ表示)\n" + output
        await ctx.reply(f"{title}\n```py\n{output}\n```")

    def _build_admin_list_text(self, ctx: commands.Context) -> str:
        admin_ids = sorted(ADMIN_IDS)
        if not admin_ids:
            return "管理者は登録されていません"

        guild = ctx.guild
        lines: list[str] = []
        for index, user_id in enumerate(admin_ids, start=1):
            member = guild.get_member(user_id) if guild else None
            if member is not None:
                label = f"{member.display_name} ({user_id})"
            else:
                label = str(user_id)
            lines.append(f"{index}: {label}")
        return "管理者一覧\n" + "\n".join(lines)

    async def _wait_for_same_user_message(self, ctx: commands.Context):
        def check(message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            return await self.bot.wait_for(
                "message",
                timeout=get_menu_timeout_seconds(),
                check=check,
            )
        except Exception:
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
    print("admin.py cog loaded")
