
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent

# Load backend environment variables.
load_dotenv(BASE_DIR / ".env", override=True)

# Support running from the backend directory or as a package.
if __package__:
    from .chat_stream import SSE_HEADERS, stream_chat_events
    from .context import prompt as profile_prompt
    from .github_catalog import GitHubSyncError, public_directory
    from .retrieval import build_grounded_messages
    from .session_store import create_session_store
    from .storage import (
        ChatMessage,
        SessionConflictError,
        validate_session_id,
    )
else:
    from chat_stream import SSE_HEADERS, stream_chat_events
    from context import prompt as profile_prompt
    from github_catalog import GitHubSyncError, public_directory
    from retrieval import build_grounded_messages
    from session_store import create_session_store
    from storage import (
        ChatMessage,
        SessionConflictError,
        validate_session_id,
    )

logger = logging.getLogger(__name__)

app = FastAPI(title="Twin AI Digital Twin API")

# --------------------------------------------------
# CORS
# --------------------------------------------------

origins = list(
    dict.fromkeys(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# --------------------------------------------------
# OpenAI client
# --------------------------------------------------

client = OpenAI(
    timeout=30.0,
    max_retries=2,
)

# --------------------------------------------------
# Session storage
# --------------------------------------------------

# Default to local storage during development.
# Future AWS deployment can use S3.
os.environ.setdefault("USE_S3", "false")

os.environ.setdefault(
    "MEMORY_DIR",
    str(BASE_DIR.parent / "memory"),
)

session_store = create_session_store()

# Existing professional profile.
PERSONALITY = profile_prompt()

# --------------------------------------------------
# Request / response models
# --------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str


# --------------------------------------------------
# API endpoints
# --------------------------------------------------

@app.get("/")
async def root():
    return {
        "message": "AI Digital Twin API with Memory",
        "storage": "S3" if os.getenv(
            "USE_S3", "false"
        ).lower() == "true" else "local",
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# Browsers may reuse the directory for a minute; Vercel's CDN for five,
# serving a stale copy while it refreshes. Syncs run every few hours, so
# new repositories appear within minutes of a sync with no redeploy.
PROJECTS_CACHE_CONTROL = (
    "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
)


@app.get("/projects")
def projects(response: Response):
    try:
        directory = public_directory()
    except GitHubSyncError:
        logger.exception("Project catalog unavailable")

        raise HTTPException(
            status_code=503,
            detail="The project catalog is temporarily unavailable.",
            headers={"Cache-Control": "no-store"},
        )

    response.headers["Cache-Control"] = PROJECTS_CACHE_CONTROL
    return directory


def _load_or_create_session(session_id: Optional[str]):
    """Return the visitor's session, or raise the HTTP error the client expects."""
    if session_id is None:
        return session_store.create()

    try:
        validate_session_id(session_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid session ID",
        )

    session = session_store.load(session_id)

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found or expired",
        )

    return session


def _build_chat_messages(
    session,
    message: str,
) -> list[ChatCompletionMessageParam]:
    """Grounded system prompt + bounded history + the current question."""
    messages: list[ChatCompletionMessageParam] = build_grounded_messages(
        PERSONALITY,
        message,
    )

    # The retrieval builder may already include
    # the current user question. Remove that copy
    # before reconstructing conversation history.
    if (
        messages
        and messages[-1].get("role") == "user"
        and messages[-1].get("content") == message
    ):
        messages.pop()

    # Add bounded conversation history.
    for item in session.messages[-40:]:
        if item.role in ("user", "assistant"):
            messages.append(
                {
                    "role": item.role,
                    "content": item.content,
                }
            )

    # Append the current question exactly once.
    messages.append(
        {
            "role": "user",
            "content": message,
        }
    )

    return messages


def _save_exchange(session, message: str, reply: str) -> None:
    session.messages.extend(
        [
            ChatMessage(role="user", content=message),
            ChatMessage(role="assistant", content=reply),
        ]
    )

    # Persist session through the selected adapter.
    session_store.save(session)


def _model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    try:
        # 1. Create or load a conversation session.
        session = _load_or_create_session(request.session_id)

        # 2-3. Grounded prompt plus bounded history.
        messages = _build_chat_messages(session, request.message)

        # 4. Generate the AI response.
        response = client.chat.completions.create(
            model=_model_name(),
            messages=messages,
        )

        assistant_response = (
            response.choices[0].message.content
        )

        if not assistant_response:
            raise HTTPException(
                status_code=502,
                detail="The AI service returned no text response.",
            )

        # 5-6. Update and persist conversation history.
        _save_exchange(session, request.message, assistant_response)

        return ChatResponse(
            response=assistant_response,
            session_id=session.session_id,
        )

    except SessionConflictError:
        raise HTTPException(
            status_code=409,
            detail=(
                "Conversation was updated concurrently. "
                "Please retry."
            ),
        )

    except HTTPException:
        raise

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired session",
        )

    except Exception:
        logger.exception("Chat request failed")

        raise HTTPException(
            status_code=500,
            detail="An internal error occurred.",
        )


@app.post("/chat/stream")
def chat_stream(request: ChatRequest):
    """Same request as /chat, but the reply arrives as server-sent events.

    Session and validation errors are returned as normal HTTP errors before
    streaming starts, so clients handle them exactly as they do for /chat.
    See chat_stream.py for the event protocol.
    """
    try:
        session = _load_or_create_session(request.session_id)
        messages = _build_chat_messages(session, request.message)

    except HTTPException:
        raise

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired session",
        )

    except Exception:
        logger.exception("Chat stream setup failed")

        raise HTTPException(
            status_code=500,
            detail="An internal error occurred.",
        )

    def open_stream():
        return client.chat.completions.create(
            model=_model_name(),
            messages=messages,
            stream=True,
        )

    events = stream_chat_events(
        session_id=session.session_id,
        open_stream=open_stream,
        on_complete=lambda reply: _save_exchange(
            session,
            request.message,
            reply,
        ),
    )

    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )
