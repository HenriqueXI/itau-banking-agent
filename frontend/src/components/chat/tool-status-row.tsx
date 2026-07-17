import { Check, Loader2 } from "lucide-react";
import type { ToolStatus } from "@/lib/agui-types";
import { copy } from "@/lib/copy";

export function ToolStatusRow({ tool }: { tool: ToolStatus }) {
  const label = copy.chat.toolLabels[tool.name] ?? tool.name;
  return (
    <details className="group rounded-full border bg-muted/60 text-xs text-muted-foreground open:rounded-xl">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-1.5 [&::-webkit-details-marker]:hidden">
        {tool.state === "finished" ? (
          <Check aria-hidden className="size-3.5 text-success" />
        ) : (
          <Loader2 aria-hidden className="size-3.5 animate-spin text-primary" />
        )}
        {label}
      </summary>
      {tool.summary && <p className="max-w-xs px-3 pb-2 pt-1 break-words">{tool.summary}</p>}
    </details>
  );
}
