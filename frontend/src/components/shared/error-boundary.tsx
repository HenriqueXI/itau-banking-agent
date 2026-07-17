"use client";

import { Component, type PropsWithChildren } from "react";
import { copy } from "@/lib/copy";

interface ErrorBoundaryState {
  error: Error | null;
  correlationId: string | null;
}

export class ErrorBoundary extends Component<PropsWithChildren, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, correlationId: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error, correlationId: crypto.randomUUID().slice(0, 8) };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="flex min-h-dvh items-center justify-center bg-background p-4">
        <section role="alert" className="w-full max-w-md rounded-xl border bg-card p-6 text-card-foreground shadow-sm">
          <h1 className="text-lg font-semibold">{copy.errors.boundaryTitle}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{copy.errors.boundaryHint}</p>
          <p className="mt-3 rounded-md bg-muted px-3 py-2 font-mono text-xs text-muted-foreground">
            {this.state.correlationId}
          </p>
          <button
            className="mt-4 inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            onClick={() => window.location.reload()}
          >
            {copy.errors.reload}
          </button>
        </section>
      </main>
    );
  }
}
