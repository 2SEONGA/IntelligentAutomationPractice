// Montage has no chart/dashboard glyph; drawn on the same 24px grid with a 1.8px rounded stroke
// so it sits beside the outlined Montage icons. Sized and colored like them (1em, currentColor).
export default function IconDashboard() {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round" aria-hidden>
      <rect x="3.4" y="3.4" width="7.6" height="9.6" rx="2" />
      <rect x="13" y="3.4" width="7.6" height="5.6" rx="2" />
      <rect x="13" y="11" width="7.6" height="9.6" rx="2" />
      <rect x="3.4" y="15" width="7.6" height="5.6" rx="2" />
    </svg>
  );
}
