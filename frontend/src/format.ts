export function fmtDuration(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return "";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export function fmtTech(format: string | null, sr: number | null, bd: number | null): string {
  if (!format) return "";
  const parts = [format];
  if (sr) parts.push(`${+(sr / 1000).toFixed(1)} kHz`);
  if (bd) parts.push(`${bd} bit`);
  return parts.join(" · ");
}

export function trackLabel(t: { movement_no: number | null; movement_name: string | null; title: string }): string {
  if (t.movement_name) return t.movement_no ? `${roman(t.movement_no)}. ${t.movement_name}` : t.movement_name;
  return t.title;
}

export function roman(n: number): string {
  if (n <= 0 || n > 50) return String(n);
  const table: [number, string][] = [[50, "L"], [40, "XL"], [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]];
  let out = "";
  for (const [v, r] of table) while (n >= v) { out += r; n -= v; }
  return out;
}

/** 'Ludwig van Beethoven' -> 'Beethoven'，用于紧凑显示 */
export function surname(name: string | null): string {
  if (!name) return "";
  const parts = name.split(/\s+/);
  const last = parts[parts.length - 1];
  return ["II", "Jr.", "Sr."].includes(last) && parts.length > 1 ? parts[parts.length - 2] : last;
}
