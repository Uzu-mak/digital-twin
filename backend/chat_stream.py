"""Server-sent events (SSE) for streamed chat replies.

Kept free of FastAPI and OpenAI imports so it can be unit tested on its own.

Event protocol, in order:
    event: meta   data: {"session_id": "<uuid>"}         always first
    event: delta  data: {"text": "<next chunk>"}         zero or more
    event: done   data: {"saved": true|false}             success
    event: error  data: {"detail": "<safe message>"}      failure (ends stream)

The session is saved only after the full reply has been generated, so a
stream that fails part-way never leaves half an answer in the history.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    # Ask proxies (nginx-style) not to buffer, so chunks reach the browser now.
    "X-Accel-Buffering": "no",
}


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one SSE message. JSON keeps newlines in text from breaking framing."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def delta_text(chunk: Any) -> str:
    """Extract the new text from an OpenAI streaming chunk, or ''."""
    choices = getattr(chunk, "choices", None) or []

    if not choices:
        return ""

    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None) if delta is not None else None

    return content if isinstance(content, str) else ""


def stream_chat_events(
    *,
    session_id: str,
    open_stream: Callable[[], Iterable[Any]],
    on_complete: Callable[[str], None],
) -> Iterator[str]:
    """Yield SSE messages for one streamed reply.

    open_stream: starts the model call and returns an iterable of chunks.
    on_complete: receives the full reply text; persists the session.
    """
    yield sse("meta", {"session_id": session_id})

    parts: list[str] = []

    try:
        for chunk in open_stream():
            text = delta_text(chunk)

            if text:
                parts.append(text)
                yield sse("delta", {"text": text})

    except Exception:
        logger.exception("Streaming chat completion failed")
        yield sse(
            "error",
            {"detail": "The AI service stopped responding. Please try again."},
        )
        return

    reply = "".join(parts)

    if not reply.strip():
        yield sse("error", {"detail": "The AI service returned no text response."})
        return

    try:
        on_complete(reply)
    except Exception:
        # The visitor already has the answer; only the history update failed
        # (for example a concurrent write). Say so without failing the reply.
        logger.exception("Could not save the streamed chat session")
        yield sse("done", {"saved": False})
        return

    yield sse("done", {"saved": True})
