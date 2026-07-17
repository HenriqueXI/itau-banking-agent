import type { UserRole } from "@/lib/agui-types";
import { Badge } from "@/components/ui/badge";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const roleVariant: Record<UserRole, "role-customer" | "role-manager" | "role-admin"> = {
  customer: "role-customer",
  manager: "role-manager",
  admin: "role-admin",
};

export function RoleBadge({ name, role, className }: { name: string; role: UserRole; className?: string }) {
  return (
    <Badge variant={roleVariant[role]} className={cn("px-2.5 py-1", className)}>
      <span
        aria-hidden
        className="flex size-5 items-center justify-center rounded-full bg-current/15 text-[0.65rem] font-bold uppercase"
      >
        {name.charAt(0)}
      </span>
      <span className="font-semibold">{name}</span>
      <span className="opacity-80">· {copy.roles[role]}</span>
    </Badge>
  );
}
