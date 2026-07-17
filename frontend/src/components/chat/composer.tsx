"use client";

import { useState } from "react";
import { SendHorizonal } from "lucide-react";
import { copy } from "@/lib/copy";
import { Button } from "@/components/ui/button";

const MAX_LENGTH = 4000;

export function Composer({ disabled, onSend }: { disabled: boolean; onSend(text: string): void }) {
  const [text, setText] = useState("");

  function send() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <div className="rounded-2xl border bg-card shadow-sm transition-shadow focus-within:ring-2 focus-within:ring-ring">
      <label className="sr-only" htmlFor="composer">
        {copy.chat.composerLabel}
      </label>
      <textarea
        id="composer"
        maxLength={MAX_LENGTH}
        rows={2}
        value={text}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        }}
        placeholder={disabled ? copy.chat.waitingConfirmation : copy.chat.composerPlaceholder}
        className="block max-h-40 w-full resize-none bg-transparent px-4 pt-3 text-sm placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
      <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
        <span className={`text-xs ${text.length > MAX_LENGTH - 200 ? "text-warning" : "text-transparent"}`} aria-hidden={text.length <= MAX_LENGTH - 200}>
          {text.length}/{MAX_LENGTH}
        </span>
        <Button type="button" size="sm" disabled={disabled || !text.trim()} onClick={send}>
          <SendHorizonal aria-hidden />
          {copy.common.send}
        </Button>
      </div>
    </div>
  );
}
