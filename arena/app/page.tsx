"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Send, Loader2, RotateCcw, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const LANGUAGES: Record<string, string> = {
  hi: "Hindi",
  bn: "Bengali",
  te: "Telugu",
  mr: "Marathi",
  ta: "Tamil",
  gu: "Gujarati",
  kn: "Kannada",
  ml: "Malayalam",
  en: "English",
  as: "Assamese",
};

interface ToolCall {
  tool_name: string;
  args: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
}

interface SessionInfo {
  session_id: string;
  baseline_model: string;
  candidate_model: string;
}

export default function ArenaPage() {
  const [langCode, setLangCode] = useState("hi");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [baselineMessages, setBaselineMessages] = useState<Message[]>([]);
  const [candidateMessages, setCandidateMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const baselineEndRef = useRef<HTMLDivElement>(null);
  const candidateEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    baselineEndRef.current?.scrollIntoView({ behavior: "smooth" });
    candidateEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [baselineMessages, candidateMessages]);

  async function startSession() {
    setStarting(true);
    setError(null);
    try {
      const res = await fetch("/api/arena/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang_code: langCode }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to start session");
      }
      const data: SessionInfo = await res.json();
      setSession(data);
      setBaselineMessages([]);
      setCandidateMessages([]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start session");
    } finally {
      setStarting(false);
    }
  }

  async function sendMessage() {
    if (!session || !input.trim() || loading) return;

    const userMsg: Message = { role: "user", content: input.trim() };
    setBaselineMessages((prev) => [...prev, userMsg]);
    setCandidateMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/arena/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          message: userMsg.content,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Chat request failed");
      }
      const data = await res.json();

      setBaselineMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.baseline.text,
          toolCalls: data.baseline.tool_calls,
        },
      ]);
      setCandidateMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.candidate.text,
          toolCalls: data.candidate.tool_calls,
        },
      ]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Chat request failed");
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setSession(null);
    setBaselineMessages([]);
    setCandidateMessages([]);
    setInput("");
    setError(null);
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <header className="flex items-center justify-between border-b px-6 py-3 shrink-0">
        <h1 className="text-lg font-semibold">Chat Arena</h1>
        <div className="flex items-center gap-3">
          <Select value={langCode} onValueChange={setLangCode} disabled={!!session}>
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(LANGUAGES).map(([code, name]) => (
                <SelectItem key={code} value={code}>
                  {name} ({code})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!session ? (
            <Button onClick={startSession} disabled={starting}>
              {starting && <Loader2 className="animate-spin" />}
              Start
            </Button>
          ) : (
            <Button variant="outline" onClick={reset}>
              <RotateCcw className="size-4" />
              Reset
            </Button>
          )}
        </div>
      </header>

      {error && (
        <div className="mx-6 mt-3 rounded-md border border-destructive/50 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Columns */}
      <div className="flex flex-1 min-h-0">
        {/* Baseline */}
        <div className="flex-1 flex flex-col border-r">
          <div className="border-b px-4 py-2 text-sm font-medium text-muted-foreground bg-muted/30">
            Baseline{session ? ` — ${session.baseline_model}` : ""}
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {baselineMessages.map((msg, i) => (
              <MessageBubble key={i} message={msg} />
            ))}
            {loading && baselineMessages[baselineMessages.length - 1]?.role === "user" && (
              <div className="flex items-center gap-2 text-muted-foreground text-sm">
                <Loader2 className="size-4 animate-spin" /> Thinking...
              </div>
            )}
            <div ref={baselineEndRef} />
          </div>
        </div>

        {/* Candidate */}
        <div className="flex-1 flex flex-col">
          <div className="border-b px-4 py-2 text-sm font-medium text-muted-foreground bg-muted/30">
            Candidate{session ? ` — ${session.candidate_model}` : ""}
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {candidateMessages.map((msg, i) => (
              <MessageBubble key={i} message={msg} />
            ))}
            {loading && candidateMessages[candidateMessages.length - 1]?.role === "user" && (
              <div className="flex items-center gap-2 text-muted-foreground text-sm">
                <Loader2 className="size-4 animate-spin" /> Thinking...
              </div>
            )}
            <div ref={candidateEndRef} />
          </div>
        </div>
      </div>

      {/* Input */}
      <div className="border-t px-6 py-4 shrink-0">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            sendMessage();
          }}
          className="flex gap-3"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              session
                ? "Type your message as a farmer..."
                : "Start a session first..."
            }
            disabled={!session || loading}
            className="flex-1 rounded-md border bg-background px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          />
          <Button type="submit" disabled={!session || loading || !input.trim()}>
            {loading ? <Loader2 className="animate-spin" /> : <Send className="size-4" />}
            Send
          </Button>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-4 py-2.5 text-sm ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted"
        }`}
      >
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {message.toolCalls.map((tc, i) => (
              <Badge key={i} variant="secondary" className="text-xs gap-1">
                <Wrench className="size-3" />
                {tc.tool_name}
              </Badge>
            ))}
          </div>
        )}
        <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
