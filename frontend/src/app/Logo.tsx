/** The SaidSigned mark: a short line (what was said) and a long one (what was signed) leading to the same point. */
export function Logo({ inverted = false }: { inverted?: boolean }) {
  return (
    <span aria-hidden="true" className={`grid h-6 w-6 place-items-center rounded ${inverted ? "bg-slate-50" : "bg-slate-900"}`}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={inverted ? "#17191E" : "#F6F5F1"} strokeWidth="2.2"
        strokeLinecap="round"><path d="M4 8h9" /><path d="M4 16h16" /><path d="M17 5l3 3-3 3" /></svg>
    </span>
  );
}
