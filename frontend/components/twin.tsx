"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowDown,
  ArrowUp,
  Bot,
  Check,
  Copy,
  RotateCcw,
  Sparkles,
  SquarePen,
  User,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// --------------------------------------------------
// Types and configuration
// --------------------------------------------------

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string; // ISO string so the history can be stored as JSON
  isError?: boolean;
  /** For failed replies: the question to resend on retry. */
  retryOf?: string;
}

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/+$/, "");

// Must match ChatRequest.message max_length in backend/server.py.
const MAX_MESSAGE_LENGTH = 4000;
const REQUEST_TIMEOUT_MS = 60_000;
// Within this distance of the bottom the user counts as "following" the chat.
const NEAR_BOTTOM_PX = 120;
// Conversation survives a page reload for the tab's lifetime.
const STORAGE_KEY = "digital-twin-chat-v1";

const suggestedQuestions = [
  "What areas of AI systems do I focus on?",
  "Tell me about AegisOps AI.",
  "What is my current manufacturing AI work?",
  "Which of my projects are deployed?",
];

// --------------------------------------------------
// Helpers
// --------------------------------------------------

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Turn an HTTP status or network failure into a message a visitor understands. */
function describeError(status?: number): string {
  if (status === 429) return "I'm getting a lot of questions right now. Please try again in a moment.";
  if (status === 400) return "That message couldn't be processed. Please rephrase it and try again.";
  if (status === 503) return "My knowledge store is briefly unavailable. Please try again shortly.";
  if (status && status >= 500) return "Something went wrong on my side. Please try again.";
  return "I couldn't reach the server. Check your connection and try again.";
}

interface StoredChat {
  sessionId: string;
  messages: Message[];
}

function loadStoredChat(): StoredChat | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredChat;
    return Array.isArray(parsed.messages) ? parsed : null;
  } catch {
    return null; // storage blocked or corrupt: start fresh
  }
}

function saveStoredChat(chat: StoredChat): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(chat));
  } catch {
    // Private mode or quota: the chat still works, it just won't survive a reload.
  }
}

// --------------------------------------------------
// Component
// --------------------------------------------------

export default function Twin() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [isNearBottom, setIsNearBottom] = useState(true);
  const [unreadCount, setUnreadCount] = useState(0);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isNearBottomRef = useRef(true);

  // ---- Restore and persist the conversation (per browser tab) ----

  useEffect(() => {
    const stored = loadStoredChat();
    if (stored) {
      setMessages(stored.messages);
      setSessionId(stored.sessionId);
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) saveStoredChat({ sessionId, messages });
  }, [hydrated, sessionId, messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // ---- Scrolling: scroll the chat log, never the page ----

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const log = logRef.current;
    if (!log) return;
    log.scrollTo({ top: log.scrollHeight, behavior });
    setUnreadCount(0);
  }, []);

  const handleScroll = () => {
    const log = logRef.current;
    if (!log) return;
    const near =
      log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM_PX;
    isNearBottomRef.current = near;
    setIsNearBottom(near);
    if (near) setUnreadCount(0);
  };

  // Follow new messages only while the reader is already at the bottom;
  // if they scrolled up to reread something, show a "new messages" pill instead.
  useLayoutEffect(() => {
    if (!hydrated) return;
    if (isNearBottomRef.current) {
      scrollToBottom(messages.length <= 1 ? "auto" : "smooth");
    }
  }, [messages, isLoading, hydrated, scrollToBottom]);

  // ---- Composer: auto-grow the textarea with its content ----

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  // ---- Sending ----

  const appendMessage = (message: Message) => {
    setMessages((prev) => [...prev, message]);
    if (!isNearBottomRef.current && message.role === "assistant") {
      setUnreadCount((count) => count + 1);
    }
  };

  const sendMessage = async (text: string) => {
    const message = text.trim();
    if (!message || isLoading || message.length > MAX_MESSAGE_LENGTH) return;

    // Sending is an explicit action: always jump to the newest content.
    isNearBottomRef.current = true;
    setIsNearBottom(true);

    appendMessage({
      id: newId(),
      role: "user",
      content: message,
      timestamp: new Date().toISOString(),
    });
    setInput("");
    setIsLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    const postChat = (activeSessionId?: string) =>
      fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: activeSessionId }),
        signal: controller.signal,
      });

    try {
      let response = await postChat(sessionId || undefined);

      // Sessions expire after an hour idle. Start a fresh one once
      // instead of failing every message until the page reloads.
      if (response.status === 404 && sessionId) {
        setSessionId("");
        response = await postChat();
      }

      if (!response.ok) {
        appendMessage({
          id: newId(),
          role: "assistant",
          content: describeError(response.status),
          timestamp: new Date().toISOString(),
          isError: true,
          retryOf: message,
        });
        return;
      }

      const data = (await response.json()) as {
        response?: unknown;
        session_id?: unknown;
      };

      if (typeof data.response !== "string" || typeof data.session_id !== "string") {
        throw new Error("The chat API returned an invalid response.");
      }

      setSessionId(data.session_id);
      appendMessage({
        id: newId(),
        role: "assistant",
        content: data.response,
        timestamp: new Date().toISOString(),
      });
    } catch (error) {
      if (controller.signal.aborted && abortRef.current !== controller) return; // chat was cleared
      console.error("Chat request failed:", error);
      appendMessage({
        id: newId(),
        role: "assistant",
        content: controller.signal.aborted
          ? "That took too long to answer. Please try again."
          : describeError(),
        timestamp: new Date().toISOString(),
        isError: true,
        retryOf: message,
      });
    } finally {
      window.clearTimeout(timeout);
      if (abortRef.current === controller) {
        abortRef.current = null;
        setIsLoading(false);
      }
    }
  };

  const retry = (failed: Message) => {
    if (!failed.retryOf || isLoading) return;
    // Drop the failed reply and the question that caused it, then resend.
    setMessages((prev) => {
      const index = prev.findIndex((m) => m.id === failed.id);
      return index > 0 ? prev.slice(0, index - 1) : prev;
    });
    void sendMessage(failed.retryOf);
  };

  const startNewChat = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsLoading(false);
    setMessages([]);
    setSessionId("");
    setUnreadCount(0);
    setInput("");
    inputRef.current?.focus();
  };

  const copyMessage = async (message: Message) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId((id) => (id === message.id ? null : id)), 1600);
    } catch {
      // Clipboard can be blocked (insecure context, permissions); nothing to do.
    }
  };

  const remaining = MAX_MESSAGE_LENGTH - input.length;
  const canSend = input.trim().length > 0 && remaining >= 0 && !isLoading;
  const hasMessages = messages.length > 0;

  return (
    <div className="twin-shell">
      {/* ---------- Header ---------- */}
      <div className="twin-header">
        <div className="twin-heading-mark">
          <Bot aria-hidden="true" size={19} />
        </div>

        <div>
          <p className="twin-kicker">AI DIGITAL TWIN</p>
          <h3>Jeremiah, in conversation</h3>
        </div>

        <div className="twin-header-actions">
          <span className="twin-status" aria-live="polite">
            <span
              className={`status-dot ${isLoading ? "status-dot-busy" : ""}`}
              aria-hidden="true"
            />
            {isLoading ? "THINKING" : "ONLINE"}
          </span>

          <button
            type="button"
            className="twin-icon-button"
            onClick={startNewChat}
            disabled={!hasMessages && !isLoading}
            aria-label="Start a new conversation"
            title="New conversation"
          >
            <SquarePen aria-hidden="true" size={15} />
            <span className="twin-icon-label">New chat</span>
          </button>
        </div>
      </div>

      {/* ---------- Conversation (scrolls inside the panel) ---------- */}
      <div className="chat-log-wrap">
        <div
          ref={logRef}
          className="chat-log"
          onScroll={handleScroll}
          role="log"
          aria-live="polite"
          aria-relevant="additions"
          aria-label="Conversation with the AI Digital Twin"
          tabIndex={0}
        >
          {!hasMessages && (
            <div className="chat-welcome">
              <div className="welcome-icon">
                <Sparkles aria-hidden="true" size={19} />
              </div>

              <p className="welcome-title">Where should we start?</p>

              <p className="welcome-copy">
                Ask about my engineering background, AI interests, or selected
                work. Answers about projects link to their source on GitHub.
              </p>

              <div className="suggested-questions" aria-label="Suggested questions">
                {suggestedQuestions.map((question) => (
                  <button
                    key={question}
                    type="button"
                    onClick={() => void sendMessage(question)}
                  >
                    {question}
                    <ArrowUp aria-hidden="true" size={13} />
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => {
            const isUser = message.role === "user";

            return (
              <article
                className={`message-row ${isUser ? "message-user" : "message-assistant"}`}
                key={message.id}
                aria-label={isUser ? "You said" : "Digital Twin replied"}
              >
                <div className={`message-avatar ${isUser ? "message-avatar-user" : ""}`}>
                  {isUser ? (
                    <User aria-hidden="true" size={15} />
                  ) : (
                    <Bot aria-hidden="true" size={16} />
                  )}
                </div>

                <div className={`message-bubble ${message.isError ? "message-error" : ""}`}>
                  <div className="message-meta">
                    <span>{isUser ? "You" : "Digital Twin"}</span>
                    {message.isError && <span className="error-label">REQUEST ISSUE</span>}
                  </div>

                  {isUser ? (
                    // User text is shown verbatim, never parsed as markdown.
                    <p className="message-plain">{message.content}</p>
                  ) : (
                    <div className="markdown-body">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ href, children }) => (
                            <a href={href} target="_blank" rel="noopener noreferrer">
                              {children}
                            </a>
                          ),
                        }}
                      >
                        {message.content}
                      </ReactMarkdown>
                    </div>
                  )}

                  <div className="message-footer">
                    <time className="message-time" dateTime={message.timestamp}>
                      {formatTime(message.timestamp)}
                    </time>

                    {!isUser && !message.isError && (
                      <button
                        type="button"
                        className="message-action"
                        onClick={() => void copyMessage(message)}
                        aria-label={copiedId === message.id ? "Copied" : "Copy answer"}
                      >
                        {copiedId === message.id ? (
                          <Check aria-hidden="true" size={12} />
                        ) : (
                          <Copy aria-hidden="true" size={12} />
                        )}
                        {copiedId === message.id ? "Copied" : "Copy"}
                      </button>
                    )}

                    {message.isError && message.retryOf && (
                      <button
                        type="button"
                        className="message-action"
                        onClick={() => retry(message)}
                        disabled={isLoading}
                      >
                        <RotateCcw aria-hidden="true" size={12} />
                        Retry
                      </button>
                    )}
                  </div>
                </div>
              </article>
            );
          })}

          {isLoading && (
            <div className="message-row message-assistant" role="status">
              <div className="message-avatar">
                <Bot aria-hidden="true" size={16} />
              </div>
              <div className="typing-indicator">
                <span aria-hidden="true" />
                <span aria-hidden="true" />
                <span aria-hidden="true" />
                <span className="typing-label">Searching my profile and repositories…</span>
              </div>
            </div>
          )}
        </div>

        {/* Shown when the reader has scrolled up and newer content exists. */}
        {!isNearBottom && hasMessages && (
          <button
            type="button"
            className="jump-latest"
            onClick={() => {
              isNearBottomRef.current = true;
              scrollToBottom();
            }}
          >
            <ArrowDown aria-hidden="true" size={13} />
            {unreadCount > 0
              ? `${unreadCount} new ${unreadCount === 1 ? "reply" : "replies"}`
              : "Jump to latest"}
          </button>
        )}
      </div>

      {/* ---------- Composer ---------- */}
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSend) void sendMessage(input);
        }}
      >
        <label className="sr-only" htmlFor="twin-message">
          Message the Digital Twin
        </label>

        <textarea
          ref={inputRef}
          id="twin-message"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; Shift+Enter adds a line; ignore Enter while an IME is composing.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (canSend) void sendMessage(input);
            }
          }}
          placeholder={isLoading ? "Waiting for the reply…" : "Ask me anything about my work…"}
          rows={1}
          maxLength={MAX_MESSAGE_LENGTH + 200}
          aria-describedby="twin-composer-hint"
          aria-invalid={remaining < 0}
        />

        <button
          className="send-button"
          type="submit"
          disabled={!canSend}
          aria-label="Send message"
        >
          <ArrowUp aria-hidden="true" size={18} />
        </button>
      </form>

      <div className="chat-footer-row">
        <p className="chat-disclaimer" id="twin-composer-hint">
          <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line ·
          Answers come from my profile and public GitHub repositories.
        </p>

        {remaining < 400 && (
          <span
            className={`char-count ${remaining < 0 ? "char-count-over" : ""}`}
            aria-live="polite"
          >
            {remaining < 0
              ? `${-remaining} characters over the limit`
              : `${remaining} characters left`}
          </span>
        )}
      </div>
    </div>
  );
}
