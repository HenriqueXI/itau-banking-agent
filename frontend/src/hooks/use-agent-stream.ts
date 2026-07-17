"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, requestStepUp } from "@/lib/api";
import type {
  AgentMessage,
  AguiEvent,
  ConfirmationPayload,
  ResumePayload,
  StepUpPayload,
  ToolStatus,
} from "@/lib/agui-types";
import { streamAgent } from "@/lib/copilot/agui-client";
import { useAuth } from "@/app/providers";
import { copy } from "@/lib/copy";
import { saveThreadTitle } from "@/lib/thread-meta";

function newThread(): string {
  return crypto.randomUUID();
}

export function useAgentStream() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { session, logout } = useAuth();
  const [threadId, setThreadId] = useState(() =>
    typeof window === "undefined" ? "thread" : sessionStorage.getItem("itau-thread") ?? newThread(),
  );
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [tools, setTools] = useState<ToolStatus[]>([]);
  const [confirmation, setConfirmation] = useState<ConfirmationPayload | null>(() => {
    if (typeof window === "undefined") return null;
    const saved = sessionStorage.getItem("itau-confirmation");
    try {
      return saved ? (JSON.parse(saved) as ConfirmationPayload) : null;
    } catch {
      return null;
    }
  });
  const [stepUp, setStepUp] = useState<StepUpPayload | null>(null);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [stepUpAttempts, setStepUpAttempts] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uiContext, setUiContext] = useState<{ selected_card_id?: string; selected_account_id?: string }>({});

  useEffect(() => {
    sessionStorage.setItem("itau-thread", threadId);
  }, [threadId]);
  useEffect(() => {
    if (confirmation) sessionStorage.setItem("itau-confirmation", JSON.stringify(confirmation));
    else sessionStorage.removeItem("itau-confirmation");
  }, [confirmation]);

  const onEvent = useMemo(
    () => (event: AguiEvent) => {
      if (event.type === "TEXT_MESSAGE_CONTENT")
        setMessages((current) => {
          const last = current.at(-1);
          const content = `${last?.role === "assistant" ? last.content : ""}${event.data.delta}`;
          return last?.role === "assistant"
            ? [...current.slice(0, -1), { ...last, content }]
            : [...current, { id: event.data.messageId, role: "assistant", content }];
        });
      if (event.type === "TOOL_CALL_START")
        setTools((current) => [...current, { id: event.data.toolCallId, name: event.data.toolCallName, state: "running" }]);
      if (event.type === "TOOL_CALL_END")
        setTools((current) =>
          current.map((tool) => (tool.id === event.data.toolCallId ? { ...tool, state: "finished", summary: event.data.result } : tool)),
        );
      if (event.type === "confirmation_required") setConfirmation(event.data);
      if (event.type === "step_up_required") setStepUp(event.data);
      if (event.type === "citations")
        setMessages((current) =>
          current.map((message, index) => (index === current.length - 1 ? { ...message, citations: event.data.citations } : message)),
        );
      if (event.type === "STATE_SNAPSHOT" && event.data.snapshot.dataChanged)
        void queryClient.invalidateQueries({ queryKey: ["account-summary"] });
      if (event.type === "RUN_ERROR")
        setError(`${event.data.message}${event.data.correlationId ? ` (${event.data.correlationId})` : ""}`);
    },
    [queryClient],
  );

  async function execute(message?: string, resume?: ResumePayload) {
    if (!session) return;
    setStreaming(true);
    setError(null);
    try {
      await streamAgent(session.token, threadId, onEvent, message, resume, uiContext);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        logout();
        router.replace(`/login?thread=${threadId}`);
        return;
      }
      setError(copy.errors.backendUnavailable);
    } finally {
      setStreaming(false);
    }
  }

  async function send(message: string) {
    saveThreadTitle(threadId, message);
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content: message }]);
    await execute(message);
    void queryClient.invalidateQueries({ queryKey: ["conversations", session?.token] });
  }

  async function resolveConfirmation(response: "confirm" | "cancel") {
    if (!confirmation) return;
    await execute(undefined, { operation_hash: confirmation.operationHash, response, stage: "confirmation" });
    setConfirmation(null);
  }

  useEffect(() => {
    if (!stepUp || !session) return;
    void requestStepUp(session.token, stepUp.operationHash)
      .then((result) => setChallengeId(result.challenge_id))
      .catch(() => setError(copy.errors.backendUnavailable));
  }, [session, stepUp]);

  async function submitStepUp(code: string) {
    if (!stepUp || !challengeId) throw new Error("Challenge indisponível");
    await execute(undefined, { operation_hash: stepUp.operationHash, response: code, stage: "step_up", challenge_id: challengeId });
    setStepUp(null);
    setChallengeId(null);
    setStepUpAttempts(0);
  }

  async function cancelStepUp() {
    setStepUpAttempts(0);
    await submitStepUp("cancel").catch(() => {
      setStepUp(null);
      setChallengeId(null);
    });
  }

  function selectThread(id: string) {
    setThreadId(id);
    setMessages([]);
    setTools([]);
    setConfirmation(null);
    setStepUp(null);
    setStepUpAttempts(0);
    setError(null);
  }

  function restoreMessages(history: AgentMessage[]) {
    setMessages(history);
    setTools([]);
    setConfirmation(null);
    setStepUp(null);
    setStepUpAttempts(0);
    setError(null);
  }

  return {
    session,
    threadId,
    messages,
    tools,
    confirmation,
    stepUp,
    stepUpAttempts,
    setStepUpAttempts,
    streaming,
    error,
    clearError: () => setError(null),
    send,
    resolveConfirmation,
    submitStepUp,
    cancelStepUp,
    selectThread,
    restoreMessages,
    newThread,
    uiContext,
    setUiContext,
  };
}
