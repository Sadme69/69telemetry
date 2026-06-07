"use client";

import { TYRE_COLORS, TYRE_SHORT } from "@/lib/constants";

interface Props {
  compound: string | null;
  life?: number | null;
}

export default function TyreIndicator({ compound, life }: Props) {
  if (!compound) return <span className="text-f1-muted text-xs">-</span>;

  const color = TYRE_COLORS[compound] || "#888";
  const label = TYRE_SHORT[compound] || "?";

  return (
    <span className="inline-flex items-center gap-1">
      <span
        className="inline-block w-4 h-4 rounded-full border border-gray-600 text-[9px] font-bold leading-4 text-center"
        style={{ backgroundColor: color, color: compound === "HARD" ? "#000" : "#FFF" }}
      >
        {label}
      </span>
      {life != null && <span className="text-xs text-f1-muted">{life}</span>}
    </span>
  );
}
