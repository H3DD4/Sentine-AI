import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { VerdictBadge } from "@/components/brand/Badges";
import { ProvenanceBar, SourceHealthPanel } from "@/components/brand/SourceBadges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useRef, useState, useCallback, useEffect } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  assessReportReadiness,
  getSources,
  REPORT_HANDOFF_STORAGE_KEY,
  streamChatMessage,
  validateFinding,
} from "@/lib/api";
import type {
  ChatMessage,
  Provenance,
  ReportReadiness,
  SourcesResponse,
  ValidationResponse,
} from "@/lib/api";
import { toast } from "sonner";
import {
  Paperclip,
  Send,
  Sparkles,
  Bot,
  User,
  X,
  FileCode,
  Shield,
  Target,
  GitBranch,
  Activity,
  Loader2,
  Plus,
  FileDown,
  PanelRightClose,
  PanelRightOpen,
  ArrowDown,
  Maximize2,
  Minimize2,
  ClipboardCheck,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/chat")({ component: ChatPage });

type Msg = {
  id: string;
  from: "user" | "ai";
  text: string;
  files?: string[];
  streaming?: boolean;
  /**
   * Which sources backed this specific message. Attached per-message rather
   * than held as page state: a conversation can span a source going down and
   * coming back, and an old answer must keep the coverage it was actually
   * produced under.
   */
  provenance?: Provenance;
};

// ── Conversation persistence ────────────────────────────────────────────────
// The conversation used to live only in component state, so navigating to
// another page and back destroyed it. sessionStorage (not localStorage) is the
// right scope: the transcript survives navigation and reloads within the tab,
// but a new tab starts clean and nothing is left on a shared analyst
// workstation after the browser closes.
const CHAT_STORAGE_KEY = "sentinel.chat.v1";

function loadStoredMessages(): Msg[] {
  try {
    const raw = sessionStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // A message persisted mid-stream would restore stuck in the "streaming"
    // state, with a cursor that never resolves and no request behind it.
    return parsed.map((m: Msg) => ({ ...m, streaming: false }));
  } catch {
    // Corrupt or unreadable storage must never take the page down — an empty
    // transcript is a recoverable state, a crashed route is not.
    return [];
  }
}

// ── Minimal markdown renderer (bold, inline code, line-breaks) ──────────────
function MarkdownText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return (
            <code key={i} className="rounded bg-muted px-1 py-0.5 text-[11px] font-medium">
              {part.slice(1, -1)}
            </code>
          );
        }
        return (
          <span key={i}>
            {part.split("\n").map((line, j, arr) => (
              <span key={j}>
                {line}
                {j < arr.length - 1 && <br />}
              </span>
            ))}
          </span>
        );
      })}
    </>
  );
}

function ChatPage() {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Msg[]>(loadStoredMessages);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [lastValidation, setLastValidation] = useState<ValidationResponse | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [readiness, setReadiness] = useState<ReportReadiness | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const stopStreamRef = useRef<(() => void) | null>(null);

  // Source health, polled independently of the conversation. The analyst needs
  // to know a corpus is down *before* asking a question that depends on it,
  // not only after reading an answer that quietly lacked it.
  const { data: sourceHealth, isLoading: healthLoading } = useQuery<SourcesResponse>({
    queryKey: ["kb-sources"],
    queryFn: () => getSources(),
    refetchInterval: 60_000,
  });

  const scrollToBottom = useCallback(() => {
    setShowJumpToLatest(false);
    setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, []);

  const handleChatScroll = useCallback(() => {
    const el = scrollAreaRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowJumpToLatest(distanceFromBottom > 180);
  }, []);

  /**
   * Follow the answer as it streams.
   *
   * Deliberately not `scrollToBottom`: that schedules a *smooth* scroll on a
   * 50ms timer, and streaming fires it once per token. Every new animation
   * pre-empted the one still running, which is the stutter you see — the view
   * never reaches the bottom because it keeps restarting the trip there.
   *
   * Instant scroll on an animation frame instead: one paint-aligned jump per
   * token batch, no competing animations. And it only follows when the analyst
   * is already near the bottom, so scrolling up to re-read an earlier answer no
   * longer yanks the view back down on the next token.
   */
  const followStream = useCallback(() => {
    const el = scrollAreaRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom > 120) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, []);

  // Persist the transcript so navigating away and back does not lose it.
  // Streaming messages are stored too — the `streaming` flag is cleared on
  // load — so a mid-answer navigation keeps the text received so far.
  useEffect(() => {
    try {
      sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // Quota exceeded or storage disabled. The conversation still works in
      // memory; only persistence is lost, which is not worth interrupting for.
    }
  }, [messages]);

  /**
   * Start a fresh conversation.
   *
   * Now that the transcript survives navigation, there has to be a deliberate
   * way to end one — otherwise every new finding inherits the previous
   * finding's context, and the model answers about the wrong host.
   */
  const clearConversation = useCallback(() => {
    stopStreamRef.current?.();
    stopStreamRef.current = null;
    setMessages([]);
    setLastValidation(null);
    setFiles([]);
    setInput("");
    setIsStreaming(false);
    setReadiness(null);
    try {
      sessionStorage.removeItem(CHAT_STORAGE_KEY);
      sessionStorage.removeItem(REPORT_HANDOFF_STORAGE_KEY);
    } catch {
      // Non-fatal; state is already cleared in memory.
    }
  }, []);

  const reportMessages = useCallback(
    () =>
      messages
        .filter((message) => !message.streaming && message.text.trim())
        .map(
          (message) =>
            ({
              role: message.from === "user" ? "user" : "assistant",
              content: message.text,
            }) as ChatMessage,
        ),
    [messages],
  );

  // Cleanup stream on unmount
  useEffect(() => {
    return () => {
      stopStreamRef.current?.();
    };
  }, []);

  // Analysis owns its scroll regions, so the document itself should never
  // move behind the transcript or the focus-mode surface.
  useEffect(() => {
    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";

    return () => {
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousBodyOverflow;
    };
  }, []);

  useEffect(() => {
    if (!focusMode) return;

    const restoreOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFocusMode(false);
    };
    window.addEventListener("keydown", restoreOnEscape);
    return () => window.removeEventListener("keydown", restoreOnEscape);
  }, [focusMode]);

  // Validation mutation (for file uploads)
  const validateMutation = useMutation({
    mutationFn: async ({
      title,
      description,
      evidenceFiles,
    }: {
      title: string;
      description: string;
      evidenceFiles: File[];
    }) => validateFinding(title, description, evidenceFiles),
    onSuccess: (result) => {
      setLastValidation(result);
      const summaryText =
        `**Validation Complete**\n\n` +
        `**Verdict:** ${result.verdict}  **Confidence:** ${Math.round(result.confidence * 100)}%\n\n` +
        `**Reasoning:** ${result.reasoning}\n\n` +
        `**Matched CVEs:** ${result.matched_cves.length > 0 ? result.matched_cves.join(", ") : "None"}\n` +
        `**Matched Techniques:** ${result.matched_techniques.length > 0 ? result.matched_techniques.join(", ") : "None"}\n` +
        `**Missing Evidence:** ${result.missing_evidence.length > 0 ? result.missing_evidence.join("; ") : "None"}\n` +
        `**Next Steps:** ${result.recommended_next_steps.join("; ")}`;

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          from: "ai",
          text: summaryText,
          // The verdict and the coverage it rests on are stored together, so
          // the message cannot be read without the caveat.
          provenance: result.sources
            ? {
                sources: result.sources,
                sources_used: result.sources_used ?? [],
                provenance: result.provenance ?? "",
                degraded: result.degraded ?? false,
                citations: result.citations ?? [],
              }
            : undefined,
        },
      ]);
      toast.success("Validation complete", {
        description: `Verdict: ${result.verdict} · ${Math.round(result.confidence * 100)}% confidence`,
      });
      if (result.degraded) {
        // A capped-confidence verdict produced under partial coverage is the
        // one an analyst is most likely to over-trust, so it gets its own alert.
        toast.warning("Partial knowledge coverage", {
          description: result.provenance,
        });
      }
      scrollToBottom();
    },
    onError: (error) => {
      toast.error("Validation failed", {
        description: error instanceof Error ? error.message : "Could not validate finding.",
      });
    },
  });

  const {
    mutate: assessReadiness,
    mutateAsync: assessReadinessAsync,
    isPending: readinessLoading,
    error: readinessError,
  } = useMutation({
    mutationFn: (conversation: ChatMessage[]) => assessReportReadiness(conversation),
    onSuccess: setReadiness,
    onError: (error) => {
      toast.error("Report readiness could not be assessed", {
        description: error instanceof Error ? error.message : "The readiness service failed.",
      });
    },
  });

  const evaluateReport = useCallback(() => {
    const conversation = reportMessages();
    if (!conversation.some((message) => message.role === "user")) {
      toast.info("Add finding information before evaluating the report.");
      return;
    }
    assessReadiness(conversation);
  }, [assessReadiness, reportMessages]);

  const openReportBuilder = useCallback(async () => {
    const conversation = reportMessages();
    if (!conversation.some((message) => message.role === "user")) {
      toast.info("Add finding information before building a report.");
      return;
    }
    try {
      const currentReadiness = readiness ?? (await assessReadinessAsync(conversation));
      setReadiness(currentReadiness);
      sessionStorage.setItem(
        REPORT_HANDOFF_STORAGE_KEY,
        JSON.stringify({
          readiness: currentReadiness,
          messages: conversation,
          finding_id: lastValidation?.finding_id ?? null,
        }),
      );
    } catch (error) {
      toast.error("Could not prepare report", {
        description:
          error instanceof Error ? error.message : "The conversation could not be prepared.",
      });
      return;
    }
    navigate({ to: "/report" });
  }, [assessReadinessAsync, lastValidation?.finding_id, navigate, readiness, reportMessages]);

  const sendStream = useCallback(
    (chatMessages: ChatMessage[]) => {
      const aiMsgId = crypto.randomUUID();
      setMessages((prev) => [...prev, { id: aiMsgId, from: "ai", text: "", streaming: true }]);
      setIsStreaming(true);
      scrollToBottom();

      let accumulated = "";

      const stop = streamChatMessage(
        chatMessages,
        (token) => {
          accumulated += token;
          setMessages((prev) =>
            prev.map((m) => (m.id === aiMsgId ? { ...m, text: accumulated } : m)),
          );
          followStream();
        },
        () => {
          setMessages((prev) =>
            prev.map((m) => (m.id === aiMsgId ? { ...m, streaming: false } : m)),
          );
          setIsStreaming(false);
          stopStreamRef.current = null;
        },
        (err) => {
          toast.error("Chat failed", { description: err.message });
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          setIsStreaming(false);
          stopStreamRef.current = null;
        },
        undefined,
        // Arrives before the first token, so the source strip is already on
        // screen while the answer is still being written.
        (provenance) => {
          setMessages((prev) => prev.map((m) => (m.id === aiMsgId ? { ...m, provenance } : m)));
        },
      );

      stopStreamRef.current = stop;
    },
    [scrollToBottom, followStream],
  );

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]);
  }, []);

  const send = useCallback(async () => {
    if (!input.trim() && files.length === 0) return;
    if (isStreaming) return;

    const userMsg: Msg = {
      id: crypto.randomUUID(),
      from: "user",
      text: input || "(attached evidence)",
      files: files.map((f) => f.name),
    };
    setReadiness(null);
    setMessages((prev) => [...prev, userMsg]);
    const capturedInput = input;
    const capturedFiles = [...files];
    setInput("");
    setFiles([]);
    scrollToBottom();

    if (capturedFiles.length > 0) {
      // File upload → run validation
      validateMutation.mutate({
        title: capturedInput.split("\n")[0].slice(0, 100) || "Evidence analysis",
        description: capturedInput || "Analyze attached evidence files",
        evidenceFiles: capturedFiles,
      });
    } else if (capturedInput.trim()) {
      // Text only → streaming chat
      const history = messages
        .filter((m) => !m.streaming)
        .map(
          (m) =>
            ({ role: m.from === "user" ? "user" : "assistant", content: m.text }) as ChatMessage,
        );
      history.push({ role: "user", content: capturedInput });
      sendStream(history);
    }
  }, [input, files, messages, isStreaming, validateMutation, sendStream, scrollToBottom]);

  const isLoading = isStreaming || validateMutation.isPending;

  return (
    <AppShell>
      {focusMode && (
        <button
          type="button"
          aria-label="Exit focus mode"
          className="fixed inset-0 z-[60] cursor-default bg-[#161b2e]/55 backdrop-blur-[3px]"
          onClick={() => setFocusMode(false)}
        />
      )}
      <div
        className={cn(
          "flex min-h-0 flex-col overflow-hidden bg-white transition-[inset,border-radius,box-shadow] duration-200",
          focusMode
            ? "fixed inset-0 z-[70] h-dvh border border-white/40 shadow-[0_28px_90px_rgba(8,12,35,0.38)] md:inset-6 md:h-auto md:rounded-md"
            : "h-[calc(100dvh-4.75rem)] md:h-screen",
        )}
        role={focusMode ? "dialog" : undefined}
        aria-modal={focusMode || undefined}
        aria-label={focusMode ? "Analysis focus mode" : undefined}
      >
        <PageHeader
          eyebrow={focusMode ? undefined : "Analysis workspace"}
          title={focusMode ? "Finding validation" : "Finding validation assistant"}
          description={
            focusMode
              ? undefined
              : "Review security findings against approved knowledge sources, attach evidence, and document a grounded validation decision."
          }
          compact
          dense={focusMode}
          actions={
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={clearConversation}
                disabled={messages.length === 0 || isStreaming}
              >
                <Plus className="h-4 w-4 mr-1.5" />
                New chat
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={evaluateReport}
                disabled={messages.length === 0 || readinessLoading || isStreaming}
              >
                <ClipboardCheck className="h-4 w-4 mr-1.5" />
                {readinessLoading ? "Evaluating..." : "Evaluate report"}
              </Button>
              <Button
                size="sm"
                onClick={openReportBuilder}
                disabled={messages.length === 0 || isStreaming}
              >
                <FileDown className="h-4 w-4 mr-1.5" />
                Build report
              </Button>
              <Sheet>
                <SheetTrigger asChild>
                  <Button variant="outline" size="sm" className="lg:hidden">
                    <PanelRightOpen className="h-4 w-4" />
                    Evidence
                  </Button>
                </SheetTrigger>
                <SheetContent side="right" className="w-[92vw] overflow-y-auto p-5 sm:max-w-md">
                  <SheetTitle className="mb-5">Evidence and validation</SheetTitle>
                  <EvidencePanel
                    sourceHealth={sourceHealth}
                    healthLoading={healthLoading}
                    lastValidation={lastValidation}
                    readiness={readiness}
                    readinessLoading={readinessLoading}
                    readinessError={readinessError}
                    onEvaluate={evaluateReport}
                    canEvaluate={
                      messages.some((message) => message.from === "user") && !isStreaming
                    }
                    onBuildReport={openReportBuilder}
                  />
                </SheetContent>
              </Sheet>
              <Button
                variant="outline"
                size="sm"
                className="hidden lg:inline-flex"
                onClick={() => setEvidenceOpen((open) => !open)}
                title={evidenceOpen ? "Hide evidence panel" : "Show evidence panel"}
              >
                {evidenceOpen ? <PanelRightClose /> : <PanelRightOpen />}
                {evidenceOpen ? "Hide evidence" : "Show evidence"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setFocusMode((active) => !active)}
                title={focusMode ? "Restore Analysis workspace" : "Open Analysis focus mode"}
              >
                {focusMode ? <Minimize2 /> : <Maximize2 />}
                {focusMode ? "Restore" : "Focus mode"}
              </Button>
            </>
          }
        />

        <div
          className={cn(
            "grid min-h-0 flex-1 grid-cols-1 overflow-hidden",
            evidenceOpen
              ? focusMode
                ? "lg:grid-cols-[minmax(0,1fr)_360px]"
                : "lg:grid-cols-[minmax(0,1fr)_320px]"
              : "lg:grid-cols-1",
          )}
        >
          {/* Chat pane */}
          <div
            className={cn(
              "relative flex min-h-0 flex-col bg-white",
              evidenceOpen && "lg:border-r lg:border-border",
            )}
          >
            <ReadinessBar
              readiness={readiness}
              loading={readinessLoading}
              error={readinessError}
              onEvaluate={evaluateReport}
              canEvaluate={messages.some((message) => message.from === "user") && !isStreaming}
            />
            <div
              ref={scrollAreaRef}
              onScroll={handleChatScroll}
              className="min-h-0 flex-1 space-y-7 overflow-y-auto overscroll-contain px-5 py-6 md:px-8 lg:px-10"
            >
              {messages.length === 0 && (
                <div className="mx-auto flex h-full max-w-xl flex-col items-start justify-center text-left text-muted-foreground">
                  <div className="mb-5 flex h-12 w-12 items-center justify-center bg-brand-navy text-white">
                    <Bot className="h-5 w-5" />
                  </div>
                  <h2 className="text-2xl font-light text-foreground">Start an evidence review</h2>
                  <p className="mt-2 max-w-md text-sm leading-relaxed">
                    Describe a finding, paste payloads, or drop evidence files (Burp exports,
                    screenshots, logs) to start AI-powered validation.
                  </p>
                  <p className="mt-4 text-xs text-muted-foreground">
                    Responses stream in real time · Ctrl+Enter to send
                  </p>
                </div>
              )}

              {messages.map((m) => (
                <div
                  key={m.id}
                  className={cn("flex gap-3", m.from === "user" ? "justify-end" : "justify-start")}
                >
                  {m.from === "ai" && (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center bg-brand-navy">
                      <Bot className="h-4 w-4 text-white" />
                    </div>
                  )}
                  <div className={cn("max-w-[82%] md:max-w-[76%]", m.from === "user" && "order-1")}>
                    <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                      {m.from === "ai" ? (
                        <>
                          <Sparkles className="h-3 w-3 text-brand-cyan" />
                          Sentinel
                          {m.streaming && (
                            <span className="ml-1 inline-block h-2 w-2 rounded-full bg-brand-cyan animate-pulse" />
                          )}
                        </>
                      ) : (
                        "You"
                      )}
                    </div>
                    <div
                      className={cn(
                        "px-4 py-3 text-sm leading-relaxed",
                        m.from === "ai" ? "bg-muted/50" : "bg-brand-navy text-white",
                      )}
                    >
                      {m.from === "ai" ? (
                        <MarkdownText text={m.text || "…"} />
                      ) : (
                        <span className="whitespace-pre-wrap">{m.text}</span>
                      )}
                      {m.files && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {m.files.map((f) => (
                            <span
                              key={f}
                              className={cn(
                                "inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-[11px]",
                                m.from === "user"
                                  ? "bg-white/20 text-white"
                                  : "bg-muted text-foreground",
                              )}
                            >
                              <FileCode className="h-3 w-3" />
                              {f}
                            </span>
                          ))}
                        </div>
                      )}
                      {m.from === "ai" && m.provenance && (
                        <ProvenanceBar
                          reports={m.provenance.sources}
                          provenance={m.provenance.provenance}
                          degraded={m.provenance.degraded}
                        />
                      )}
                    </div>
                  </div>
                  {m.from === "user" && (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center border border-border bg-white">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              ))}

              <div ref={chatEndRef} />
            </div>

            {showJumpToLatest && (
              <Button
                variant="outline"
                size="sm"
                className="absolute bottom-32 left-1/2 z-10 -translate-x-1/2 bg-white shadow-soft"
                onClick={scrollToBottom}
              >
                <ArrowDown className="h-4 w-4" />
                Latest message
              </Button>
            )}

            {/* Composer */}
            <div className="shrink-0 border-t border-border bg-[#f7f8f9] p-3 md:px-8 md:py-4">
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
                className={cn(
                  "relative border bg-white transition-[border-color,box-shadow]",
                  dragOver ? "border-brand-cyan shadow-soft" : "border-input",
                )}
              >
                {files.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 p-2 pb-0">
                    {files.map((f, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1 rounded-sm bg-muted px-2 py-1 text-[11px]"
                      >
                        <FileCode className="h-3 w-3 text-brand-cyan" />
                        {f.name}
                        <button
                          onClick={() => setFiles((x) => x.filter((_, j) => j !== i))}
                          className="ml-0.5 hover:text-destructive"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
                <Textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
                  }}
                  placeholder="Describe the finding, paste technical evidence, or attach supporting files..."
                  className="max-h-40 min-h-16 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
                  disabled={isLoading}
                />
                <div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-2 sm:px-3">
                  <div className="flex items-center gap-1">
                    <input
                      ref={fileRef}
                      type="file"
                      multiple
                      className="hidden"
                      onChange={(e) => setFiles((f) => [...f, ...Array.from(e.target.files ?? [])])}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => fileRef.current?.click()}
                      disabled={isLoading}
                    >
                      <Paperclip className="h-4 w-4 mr-1" />
                      Attach
                    </Button>
                    <span className="hidden text-[11px] text-muted-foreground sm:inline">
                      or drop files here
                    </span>
                  </div>
                  <div className="flex gap-2">
                    {isStreaming && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          stopStreamRef.current?.();
                          setIsStreaming(false);
                        }}
                      >
                        Stop
                      </Button>
                    )}
                    <Button
                      onClick={send}
                      disabled={isLoading && !isStreaming}
                      className="min-w-24 sm:min-w-28"
                    >
                      {validateMutation.isPending ? (
                        <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                      ) : (
                        <Send className="h-4 w-4 mr-1.5" />
                      )}
                      Analyze
                      <span className="ml-2 text-[10px] opacity-70">⌘↵</span>
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Right: validation result panel */}
          {evidenceOpen && (
            <aside className="hidden min-h-0 overflow-y-auto border-l border-border bg-[#f4f4f4] p-5 lg:block">
              <EvidencePanel
                sourceHealth={sourceHealth}
                healthLoading={healthLoading}
                lastValidation={lastValidation}
                readiness={readiness}
                readinessLoading={readinessLoading}
                readinessError={readinessError}
                onEvaluate={evaluateReport}
                canEvaluate={messages.some((message) => message.from === "user") && !isStreaming}
                onBuildReport={openReportBuilder}
              />
            </aside>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function ReadinessBar({
  readiness,
  loading,
  error,
  onEvaluate,
  canEvaluate,
}: {
  readiness: ReportReadiness | null;
  loading: boolean;
  error: Error | null;
  onEvaluate: () => void;
  canEvaluate: boolean;
}) {
  const score = readiness?.score ?? 0;
  const percentage = score * 10;
  return (
    <div className="shrink-0 border-b border-border bg-white px-5 py-3 md:px-8 lg:px-10">
      <div className="flex items-center gap-4">
        <div className="hidden h-8 w-8 shrink-0 items-center justify-center border border-border bg-[#fafafa] sm:flex">
          <ClipboardCheck className="h-4 w-4 text-brand-cyan" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
            <span className="font-semibold text-brand-navy">Report readiness</span>
            <span className="shrink-0 text-sm font-semibold tabular-nums text-foreground">
              {loading ? "Assessing..." : `${score.toFixed(1)} / 10`}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden bg-muted" aria-label="Report readiness progress">
            <div
              className={cn(
                "h-full transition-[width,background-color] duration-500",
                readiness?.status === "ready"
                  ? "bg-verdict-confirmed"
                  : readiness?.eligible
                    ? "bg-brand-cyan"
                    : "bg-muted-foreground",
              )}
              style={{ width: `${percentage}%` }}
            />
          </div>
          <div className="mt-1.5 text-[11px] text-muted-foreground">
            {error
              ? "Assessment unavailable. Check the backend connection and retry with the next message."
              : readiness
                ? readiness.summary
                : "Evaluation is manual. Click Evaluate when you want a score and missing-section review."}
          </div>
          {readiness && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
              <span className="text-verdict-confirmed">
                Present: {readiness.strengths.join(", ") || "No complete sections yet"}
              </span>
              <span className="text-sev-medium">
                Missing: {readiness.missing.join(", ") || "None"}
              </span>
            </div>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={onEvaluate} disabled={!canEvaluate || loading}>
          {loading ? "Evaluating..." : "Evaluate"}
        </Button>
      </div>
    </div>
  );
}

function EvidencePanel({
  sourceHealth,
  healthLoading,
  lastValidation,
  readiness,
  readinessLoading,
  readinessError,
  onEvaluate,
  canEvaluate,
  onBuildReport,
}: {
  sourceHealth?: SourcesResponse;
  healthLoading: boolean;
  lastValidation: ValidationResponse | null;
  readiness: ReportReadiness | null;
  readinessLoading: boolean;
  readinessError: Error | null;
  onEvaluate: () => void;
  canEvaluate: boolean;
  onBuildReport: () => void;
}) {
  return (
    <div className="space-y-6">
      <div>
        <div className="mb-2 text-xs font-semibold text-brand-navy">Knowledge sources</div>
        <Card className="border-border bg-white p-4 shadow-soft">
          <SourceHealthPanel sources={sourceHealth?.sources ?? []} loading={healthLoading} />
        </Card>
      </div>

      <ReportAssessment
        readiness={readiness}
        loading={readinessLoading}
        error={readinessError}
        onEvaluate={onEvaluate}
        canEvaluate={canEvaluate}
      />

      <div>
        <div className="mb-2 text-xs font-semibold text-brand-navy">Validation result</div>
        <Card className="space-y-5 border-border bg-white p-5 shadow-soft">
          {lastValidation ? (
            <>
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Verdict
                </div>
                <div className="mt-2">
                  <VerdictBadge verdict={lastValidation.verdict} size="lg" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border">
                <Metric
                  icon={Shield}
                  label="CVEs"
                  value={lastValidation.matched_cves?.join(", ") || "None"}
                />
                <Metric
                  icon={Target}
                  label="MITRE"
                  value={lastValidation.matched_techniques?.slice(0, 2).join(", ") || "None"}
                />
                <Metric
                  icon={Activity}
                  label="Confidence"
                  value={`${Math.round(lastValidation.confidence * 100)}%`}
                  accent="verdict-confirmed"
                />
                <Metric
                  icon={GitBranch}
                  label="Next Steps"
                  value={String(lastValidation.recommended_next_steps?.length || 0)}
                />
              </div>
              <div>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Reasoning
                </div>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {lastValidation.reasoning}
                </p>
              </div>
              {lastValidation.missing_evidence?.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Missing Evidence
                  </div>
                  <ul className="text-sm text-sev-critical list-disc list-inside space-y-1">
                    {lastValidation.missing_evidence.map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
              {lastValidation.recommended_next_steps?.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Recommended Steps
                  </div>
                  <ol className="text-sm text-muted-foreground list-decimal list-inside space-y-1">
                    {lastValidation.recommended_next_steps.map((step, i) => (
                      <li key={i}>{step}</li>
                    ))}
                  </ol>
                </div>
              )}
              {lastValidation.citations && lastValidation.citations.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Evidence Cited
                  </div>
                  <ul className="space-y-1.5">
                    {lastValidation.citations.slice(0, 6).map((c) => (
                      <li key={`${c.source}:${c.id}`} className="text-xs leading-snug">
                        <span className="text-[10px] font-semibold text-brand-cyan">
                          [{c.source_label}]
                        </span>{" "}
                        {c.url ? (
                          <a
                            href={c.url}
                            target="_blank"
                            rel="noreferrer"
                            className="hover:underline"
                          >
                            {c.title}
                          </a>
                        ) : (
                          c.title
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {lastValidation.sources && (
                <ProvenanceBar
                  reports={lastValidation.sources}
                  provenance={lastValidation.provenance}
                  degraded={lastValidation.degraded}
                />
              )}

              {/* A validated finding is saved server-side, so the report
                    builder can already select it. Without this link the
                    connection between "I just validated something" and "I can
                    now report on it" was invisible — the report page was
                    reachable only from the sidebar, with no indication it had
                    anything to work with. */}
              <Button className="w-full" onClick={onBuildReport} disabled={readinessLoading}>
                <FileDown className="h-4 w-4" />
                {readinessLoading ? "Assessing report..." : "Build engagement report"}
              </Button>
            </>
          ) : (
            <div className="text-center py-8 text-sm text-muted-foreground">
              Send a message or drop evidence files to see validation results here.
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function ReportAssessment({
  readiness,
  loading,
  error,
  onEvaluate,
  canEvaluate,
}: {
  readiness: ReportReadiness | null;
  loading: boolean;
  error: Error | null;
  onEvaluate: () => void;
  canEvaluate: boolean;
}) {
  const draftFields = readiness
    ? [
        ["Finding", readiness.draft.title],
        ["Affected scope", readiness.draft.affected_scope],
        ["Description", readiness.draft.description],
        ["Technical evidence", readiness.draft.technical_evidence],
        ["Reproduction", readiness.draft.reproduction_steps.join("\n")],
        ["Impact", readiness.draft.impact],
        [
          "Risk rating",
          [
            readiness.draft.severity,
            readiness.draft.cvss_score != null
              ? `CVSS ${readiness.draft.cvss_score.toFixed(1)}`
              : "",
            readiness.draft.cvss_vector,
          ]
            .filter(Boolean)
            .join(" · "),
        ],
        ["CVE references", readiness.draft.matched_cves.join(", ")],
        ["ATT&CK mapping", readiness.draft.matched_techniques.join(", ")],
      ]
    : [];

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-xs font-semibold text-brand-navy">Report assessment</div>
        {readiness && (
          <span className="text-xs font-semibold tabular-nums text-brand-navy">
            {readiness.score.toFixed(1)} / {readiness.maximum.toFixed(0)}
          </span>
        )}
      </div>
      <Card className="space-y-4 border-border bg-white p-5 shadow-soft">
        {loading ? (
          <div className="flex items-center gap-2 py-5 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" />
            Extracting and scoring the current report draft...
          </div>
        ) : error ? (
          <div className="space-y-3">
            <div className="flex gap-2 text-sm text-sev-critical">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Evaluation failed. No readiness score was produced.</span>
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">{error.message}</p>
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={onEvaluate}
              disabled={!canEvaluate}
            >
              Evaluate again
            </Button>
          </div>
        ) : readiness ? (
          <>
            <div>
              <div className="mb-2 h-1.5 overflow-hidden bg-muted">
                <div
                  className={cn(
                    "h-full",
                    readiness.status === "ready" ? "bg-verdict-confirmed" : "bg-brand-cyan",
                  )}
                  style={{ width: `${readiness.score * 10}%` }}
                />
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">{readiness.summary}</p>
            </div>

            {readiness.assessment_notice && (
              <div className="flex gap-2 border border-sev-medium/30 bg-sev-medium/5 p-3 text-xs leading-relaxed text-sev-medium">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{readiness.assessment_notice}</span>
              </div>
            )}

            <div className="space-y-2 border-t border-border pt-4">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Scored sections
              </div>
              {readiness.dimensions.map((dimension) => (
                <div key={dimension.key} className="flex items-center gap-2 text-xs">
                  {dimension.complete ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-verdict-confirmed" />
                  ) : (
                    <AlertCircle className="h-3.5 w-3.5 shrink-0 text-sev-medium" />
                  )}
                  <span className="min-w-0 flex-1 text-foreground">{dimension.label}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {dimension.score.toFixed(1)} / {dimension.max_score.toFixed(1)}
                  </span>
                </div>
              ))}
            </div>

            <div className="border-t border-border pt-4">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Missing or incomplete
              </div>
              {readiness.missing.length > 0 ? (
                <ul className="space-y-1.5 text-xs text-sev-critical">
                  {readiness.missing.map((item) => (
                    <li key={item} className="flex gap-2">
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="flex gap-2 text-xs text-verdict-confirmed">
                  <CheckCircle2 className="h-3.5 w-3.5" /> All scored sections are complete.
                </div>
              )}
            </div>

            <div className="space-y-3 border-t border-border pt-4">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Extracted report details
              </div>
              {draftFields.map(([label, value]) => (
                <div key={label}>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {label}
                  </div>
                  <p
                    className={cn(
                      "mt-0.5 whitespace-pre-line text-xs leading-relaxed",
                      value ? "text-foreground" : "italic text-sev-medium",
                    )}
                  >
                    {value || "Not provided"}
                  </p>
                </div>
              ))}
            </div>

            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={onEvaluate}
              disabled={!canEvaluate}
            >
              Evaluate again
            </Button>
          </>
        ) : (
          <div className="space-y-3 py-2">
            <p className="text-sm leading-relaxed text-muted-foreground">
              Evaluate the conversation to see the report details, section scores, and content that
              is still missing.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={onEvaluate}
              disabled={!canEvaluate}
            >
              Evaluate report
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  accent = "brand-cyan",
}: {
  icon: typeof Shield;
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="border border-border bg-[#fafafa] p-2.5">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold text-muted-foreground">
        <Icon className="h-3 w-3" style={{ color: `var(--${accent})` }} />
        {label}
      </div>
      <div
        className="mt-1 truncate text-sm font-semibold tabular-nums"
        style={{ color: `var(--${accent})` }}
      >
        {value}
      </div>
    </div>
  );
}
