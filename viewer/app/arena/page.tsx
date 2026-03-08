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
import { Send, Loader2, RotateCcw, Wrench, Sun, Moon } from "lucide-react";
import { useTheme } from "next-themes";
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

type ArenaMode = "both" | "baseline" | "candidate";

interface ToolCall {
  tool_name: string;
  args: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
  error?: string;
}

interface SessionInfo {
  session_id: string;
  baseline_model: string;
  candidate_model: string;
}

export default function ArenaPage() {
  const [langCode, setLangCode] = useState("hi");
  const [mode, setMode] = useState<ArenaMode>("both");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [baselineMessages, setBaselineMessages] = useState<Message[]>([]);
  const [candidateMessages, setCandidateMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { theme, setTheme } = useTheme();
  const baselineEndRef = useRef<HTMLDivElement>(null);
  const candidateEndRef = useRef<HTMLDivElement>(null);

  const showBaseline = mode === "both" || mode === "baseline";
  const showCandidate = mode === "both" || mode === "candidate";

  useEffect(() => {
    baselineEndRef.current?.scrollIntoView({ behavior: "smooth" });
    candidateEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [baselineMessages, candidateMessages]);

  async function sendMessage() {
    if (!input.trim() || loading) return;

    // Auto-start session if needed
    let currentSession = session;
    if (!currentSession) {
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
        currentSession = await res.json();
        setSession(currentSession);
        setBaselineMessages([]);
        setCandidateMessages([]);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to start session");
        setStarting(false);
        return;
      }
      setStarting(false);
    }

    const userMsg: Message = { role: "user", content: input.trim() };
    if (showBaseline) setBaselineMessages((prev) => [...prev, userMsg]);
    if (showCandidate) setCandidateMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/arena/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: currentSession!.session_id,
          message: userMsg.content,
          mode,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Chat request failed");
      }
      const data = await res.json();

      if (data.baseline) {
        setBaselineMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: data.baseline.text ?? "",
            toolCalls: data.baseline.tool_calls,
            error: data.baseline.error ?? undefined,
          },
        ]);
      }
      if (data.candidate) {
        setCandidateMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: data.candidate.text ?? "",
            toolCalls: data.candidate.tool_calls,
            error: data.candidate.error ?? undefined,
          },
        ]);
      }
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
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            <Sun className="size-4 dark:hidden" />
            <Moon className="hidden size-4 dark:block" />
          </Button>
          <Select value={mode} onValueChange={(v) => { setMode(v as ArenaMode); reset(); }} disabled={!!session}>
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="both">Both Models</SelectItem>
              <SelectItem value="baseline">Baseline Only</SelectItem>
              <SelectItem value="candidate">Candidate Only</SelectItem>
            </SelectContent>
          </Select>
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
          {session && (
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
        {showBaseline && (
          <div className={`flex-1 flex flex-col ${showCandidate ? "border-r" : ""}`}>
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
        )}

        {showCandidate && (
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
        )}
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
            placeholder="Type your message as a farmer..."
            disabled={loading || starting}
            className="flex-1 rounded-md border bg-background px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          />
          <Button type="submit" disabled={loading || starting || !input.trim()}>
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

  if (message.error) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[85%] rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-2.5 text-sm text-destructive">
          Error: {message.error}
        </div>
      </div>
    );
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-4 py-2.5 text-sm ${
          isUser
            ? "bg-foreground text-background"
            : "bg-muted text-foreground"
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
        <div className={`prose prose-sm max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 ${isUser ? "[&_*]:!text-background" : "dark:prose-invert"}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
