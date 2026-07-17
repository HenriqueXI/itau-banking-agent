import Link from "next/link";
import { ShieldX } from "lucide-react";
import { copy } from "@/lib/copy";
import { buttonVariants } from "@/components/ui/button";

export default function ForbiddenPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background p-4">
      <section className="w-full max-w-md animate-card-in rounded-xl border bg-card p-8 text-center shadow-sm">
        <span className="mx-auto flex size-14 items-center justify-center rounded-full bg-warning/15 text-warning">
          <ShieldX aria-hidden className="size-7" />
        </span>
        <h1 className="mt-4 text-xl font-semibold tracking-tight">{copy.forbidden.title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{copy.forbidden.description}</p>
        <div className="mt-6 flex justify-center gap-3">
          <Link href="/" className={buttonVariants({ variant: "default" })}>
            {copy.forbidden.backToChat}
          </Link>
          <Link href="/login" className={buttonVariants({ variant: "outline" })}>
            {copy.forbidden.switchProfile}
          </Link>
        </div>
      </section>
    </main>
  );
}
