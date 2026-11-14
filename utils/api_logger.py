

import asyncio
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone

import aiohttp


# 現在実行中のコマンド名
_current_command = ContextVar(
    "discord_api_command",
    default="SYSTEM",
)

# 保存キュー
_queue = None
_worker_task = None

QUEUE_MAX_SIZE = 10000
BATCH_SIZE = 50
BATCH_INTERVAL = 1.0

_warned_config = False
_dropped_count = 0


def run_with_command_context(command: str, coroutine):
    """
    コマンド名をContextVarに設定して非同期処理を実行する。
    """
    async def runner():
        token = _current_command.set(command)

        try:
            return await coroutine
        finally:
            _current_command.reset(token)

    return runner()


def _enqueue(record: dict):
    global _queue, _worker_task, _dropped_count

    if _queue is None:
        _queue = asyncio.Queue(maxsize=QUEUE_MAX_SIZE)

    try:
        _queue.put_nowait(record)
    except asyncio.QueueFull:
        _dropped_count += 1
        return

    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_writer())


def create_trace_config():
    """
    Discord APIのHTTP通信を1試行ずつ記録する。
    """

    trace = aiohttp.TraceConfig()

    async def on_request_start(session, ctx, params):
        ctx.started_at = time.perf_counter()
        ctx.command = _current_command.get()
        ctx.method = params.method
        ctx.endpoint = str(params.url).split("?", 1)[0]

    async def on_request_end(session, ctx, params):
        response = params.response
        headers = response.headers

        elapsed = int(
            (time.perf_counter() - ctx.started_at) * 1000
        )

        scope = headers.get("X-RateLimit-Scope")
        global_header = headers.get("X-RateLimit-Global")

        record = {
            "requested_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "command": ctx.command,
            "method": ctx.method,
            "endpoint": ctx.endpoint,
            "status": response.status,
            "elapsed_ms": elapsed,
            "retry_after": _float_or_none(
                headers.get("Retry-After")
            ),
            "attempt": 1,
            "rate_limit_scope": scope,
            "is_global": (
                str(global_header).lower() == "true"
                if global_header is not None
                else scope == "global"
            ),
            "error": None,
        }

        _enqueue(record)

    async def on_request_exception(session, ctx, params):
        elapsed = int(
            (time.perf_counter() - ctx.started_at) * 1000
        )

        record = {
            "requested_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "command": ctx.command,
            "method": ctx.method,
            "endpoint": ctx.endpoint,
            "status": None,
            "elapsed_ms": elapsed,
            "retry_after": None,
            "attempt": 1,
            "rate_limit_scope": None,
            "is_global": None,
            "error": str(params.exception)[:500],
        }

        _enqueue(record)

    trace.on_request_start.append(on_request_start)
    trace.on_request_end.append(on_request_end)
    trace.on_request_exception.append(on_request_exception)

    return trace


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _writer():
    """
    キューに貯めたログをまとめてSupabaseに送信する。
    """

    global _warned_config

    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret_key = os.getenv("SUPABASE_SECRET_KEY", "")

    if not base_url or not secret_key:
        if not _warned_config:
            print(
                "[API LOGGER] Supabaseの環境変数が不足しています",
                flush=True,
            )
            _warned_config = True
        return

    url = f"{base_url}/rest/v1/discord_api_logs"

    headers = {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        while True:
            first = await _queue.get()
            batch = [first]

            deadline = asyncio.get_running_loop().time() + BATCH_INTERVAL

            while len(batch) < BATCH_SIZE:
                remaining = (
                    deadline
                    - asyncio.get_running_loop().time()
                )

                if remaining <= 0:
                    break

                try:
                    item = await asyncio.wait_for(
                        _queue.get(),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    break

                batch.append(item)

            saved = False

            for attempt in range(3):
                try:
                    async with session.post(
                        url,
                        headers=headers,
                        json=batch,
                    ) as response:

                        if response.status < 300:
                            saved = True
                            break

                        error_text = await response.text()

                        if response.status < 500:
                            break

                except Exception:
                    pass

                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)

            if not saved:
                # Supabaseへの保存失敗はDiscord Botを停止させない
                pass

            for _ in batch:
                _queue.task_done()


async def close():
    """
    Bot終了時にキューの保存を待つ。
    """

    global _worker_task

    if _queue is None or _worker_task is None:
        return

    try:
        await asyncio.wait_for(
            _queue.join(),
            timeout=8,
        )
    except asyncio.TimeoutError:
        pass

    if not _worker_task.done():
        _worker_task.cancel()

        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
