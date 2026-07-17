"use client";

import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/hooks/use-theme";

export function ThemeToggle() {
  const { dark, toggle } = useTheme();
  const label = dark ? "Tema claro" : "Tema escuro";
  return (
    <Button variant="ghost" size="icon" aria-pressed={dark} aria-label={label} title={label} onClick={toggle}>
      {dark ? <Sun /> : <Moon />}
    </Button>
  );
}
