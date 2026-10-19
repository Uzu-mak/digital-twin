"use client";

import { useState, useRef, useEffect } from "react";
import { ArrowUp, Bot, Sparkles, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  isError?: boolean;
}

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/+$/, "");

const suggestedQuestions = [
  "What areas of AI systems do I focus on?",
  "Tell me about AegisOps AI.",
  "What is my current manufacturing AI work?",
];

export default function Twin() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageIdRef = useRef(0);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [messages, isLoading]);

  const sendMessage = async (suggestion?: string) => {
    const message = (suggestion ?? input).trim();
    if (!message || isLoading) return;

    const userMessage: Message = {
      id: String(++messageIdRef.current),
      role: "user",
      content: message,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
          session_id: sessionId || undefined,
        }),
      });

      if (!response.ok) {
        throw new Error(`Chat request failed with status ${response.status}`);
      }

      const data = (await response.json()) as {
        response?: unknown;
        session_id?: unknown;
      };

      if (
        typeof data.response !== "string" ||
        typeof data.session_id !== "string"
      ) {
        throw new Error("The chat API returned an invalid response.");
      }

      if (!sessionId) {
        setSessionId(data.session_id);
      }

      const assistantMessage: Message = {
        id: String(++messageIdRef.current),
        role: "assistant",
        content: data.response,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      console.error("Error:", error);

      const errorMessage: Message = {
        id: String(++messageIdRef.current),
        role: "assistant",
        content: "Sorry, I encountered an error. Please try again.",
        timestamp: new Date(),
        isError: true,
      };

      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="twin-shell">
      <div className="twin-header">
        <div className="twin-heading-mark">
          <Bot aria-hidden="true" size={19} />
        </div>

        <div>
          <p className="twin-kicker">AI DIGITAL TWIN</p>
          <h3>Jeremiah, in conversation</h3>
        </div>

        <span className="twin-status">EMBEDDED CHAT</span>
      </div>

      <div
        className="chat-log"
        role="log"
        aria-live="polite"
        aria-label="Conversation with the AI Digital Twin"
      >
        {messages.length === 0 && (
          <div className="chat-welcome">
            <div className="welcome-icon">
              <Sparkles aria-hidden="true" size={19} />
            </div>

            <p className="welcome-title">Where should we start?</p>

            <p className="welcome-copy">
              Ask about my engineering background, AI interests, or selected
              work.
            </p>

            <div
              className="suggested-questions"
              aria-label="Suggested questions"
            >
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

        {messages.map((message) => (
          <div
            className={`message-row ${
              message.role === "user" ? "message-user" : "message-assistant"
            }`}
            key={message.id}
          >
            <div
              className={`message-avatar ${
                message.role === "user" ? "message-avatar-user" : ""
              }`}
            >
              {message.role === "user" ? (
                <User aria-hidden="true" size={15} />
              ) : (
                <Bot aria-hidden="true" size={16} />
              )}
            </div>

            <div
              className={`message-bubble ${
                message.isError ? "message-error" : ""
              }`}
            >
              <div className="message-meta">
                <span>{message.role === "user" ? "You" : "Digital Twin"}</span>

                {message.isError && (
                  <span className="error-label">REQUEST ISSUE</span>
                )}
              </div>

              <div className="markdown-body">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noreferrer">
                        {children}
                      </a>
                    ),
                  }}
                >
                  {message.content}
                </ReactMarkdown>
              </div>

              <time
                className="message-time"
                dateTime={message.timestamp.toISOString()}
              >
                {message.timestamp.toLocaleTimeString([], {
                  hour: "numeric",
                  minute: "2-digit",
                })}
              </time>
            </div>
          </div>
        ))}

        {isLoading && (
          <div
            className="message-row message-assistant"
            role="status"
            aria-label="Digital Twin is responding"
          >
            <div className="message-avatar">
              <Bot aria-hidden="true" size={16} />
            </div>

            <div className="typing-indicator" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void sendMessage();
        }}
      >
        <label className="sr-only" htmlFor="twin-message">
          Message the Digital Twin
        </label>

        <textarea
          id="twin-message"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void sendMessage();
            }
          }}
          placeholder="Ask a question..."
          rows={1}
          disabled={isLoading}
          aria-describedby="twin-composer-hint"
        />

        <span className="sr-only" id="twin-composer-hint">
          Press Enter to send. Use Shift and Enter for a new line.
        </span>

        <button
          className="send-button"
          type="submit"
          disabled={!input.trim() || isLoading}
          aria-label="Send message"
        >
          <ArrowUp aria-hidden="true" size={18} />
        </button>
      </form>

      <p className="chat-disclaimer">
        Responses are generated from the profile context currently available.
      </p>
    </div>
  );
}
