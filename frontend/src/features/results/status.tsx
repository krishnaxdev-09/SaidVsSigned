// The four approved result statuses (doc 08 §2) in the Ledger design: each has its own icon and shade, so they can
// be told apart without relying on hue alone.
import { useTranslation } from "react-i18next";
import type { Status } from "../../lib/types";

export const STATUS_ORDER: Status[] = ["CONFLICT", "UNCLEAR", "MATCH", "NOT_VERIFIED"];

export const STATUS_TONE: Record<Status, { chip: string; tile: string; fg: string }> = {
  CONFLICT: { chip: "bg-mismatch-bg text-mismatch-fg", tile: "bg-mismatch-bg text-mismatch-fg", fg: "text-mismatch-fg" },
  UNCLEAR: { chip: "bg-clarify-bg text-clarify-fg", tile: "bg-clarify-bg text-clarify-fg", fg: "text-clarify-fg" },
  MATCH: { chip: "bg-match-bg text-match-fg", tile: "bg-match-bg text-match-fg", fg: "text-match-fg" },
  NOT_VERIFIED: { chip: "bg-unchecked-bg text-unchecked-fg", tile: "bg-unchecked-bg text-unchecked-fg", fg: "text-unchecked-fg" },
};

export function StatusIcon({ status, size = 14 }: { status: Status; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2.2,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (status) {
    case "CONFLICT":
      return <svg {...common}><path d="M12 3l10 18H2z" /><path d="M12 10v4" /><path d="M12 17.5v.5" /></svg>;
    case "UNCLEAR":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.4v.3" /><path d="M12 17v.5" /></svg>;
    case "MATCH":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M8 12.5l3 3 5-6" /></svg>;
    default:
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M8 12h8" /></svg>;
  }
}

export function StatusChip({ status, extra }: { status: Status; extra?: string }) {
  const { t } = useTranslation();
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${STATUS_TONE[status].chip}`}>
      <StatusIcon status={status} size={12} />{t(`status_${status}`)}{extra ? ` · ${extra}` : ""}
    </span>
  );
}

export const PlayIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>
);
