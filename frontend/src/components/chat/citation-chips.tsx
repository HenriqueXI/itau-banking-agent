import { FileText } from "lucide-react";
import type { Citation } from "@/lib/agui-types";
import { copy } from "@/lib/copy";

export function CitationChips({ citations }: { citations: Citation[] }) {
  return (
    <div aria-label={copy.chat.sources} className="mt-2 flex flex-wrap gap-2">
      {citations.map((citation) => (
        <details key={`${citation.documentId}-${citation.section}`} className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground [&::-webkit-details-marker]:hidden">
            <FileText aria-hidden className="size-3.5 text-primary" />
            {citation.title} — {citation.section}
          </summary>
          <p className="mt-1.5 max-w-sm rounded-lg border bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
            {citation.marker}
          </p>
        </details>
      ))}
    </div>
  );
}
