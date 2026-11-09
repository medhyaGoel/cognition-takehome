/**
 * Money is integer minor units (cents) end to end. These helpers never go through floats:
 * parsing is string based and formatting hands Intl an exact decimal string.
 */

export type ParseResult = { ok: true; cents: number } | { ok: false; error: string };

const AMOUNT_RE = /^(\d{1,3}(?:,\d{3})+|\d+)?(?:\.(\d{0,2}))?$/;

/** "1,234.5" -> 123450. Rejects negatives, >2 decimals, exponents, and unsafe integers. */
export function parseAmountToCents(input: string): ParseResult {
  const s = input.trim().replace(/^\$\s*/, "");
  if (s === "" || s === ".") return { ok: false, error: "Enter an amount" };
  if (s.startsWith("-")) return { ok: false, error: "Amount must be positive" };
  const m = AMOUNT_RE.exec(s);
  if (!m) {
    if (/^[\d,]*\.\d{3,}$/.test(s)) return { ok: false, error: "At most 2 decimal places" };
    return { ok: false, error: "Enter a valid amount, e.g. 125.50" };
  }
  const whole = (m[1] ?? "0").replace(/,/g, "");
  const frac = (m[2] ?? "").padEnd(2, "0");
  const cents = BigInt(whole) * 100n + BigInt(frac || "0");
  if (cents <= 0n) return { ok: false, error: "Amount must be greater than 0" };
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, error: "Amount is too large" };
  return { ok: true, cents: Number(cents) };
}

/** 123450 -> "1234.50" (exact; no thousands separators). */
export function centsToDecimalString(cents: number): string {
  if (!Number.isSafeInteger(cents)) throw new RangeError("cents must be a safe integer");
  const neg = cents < 0;
  const abs = BigInt(Math.abs(cents));
  const whole = abs / 100n;
  const frac = (abs % 100n).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${whole}.${frac}`;
}

const formatters = new Map<string, Intl.NumberFormat>();

export function formatCents(cents: number, currency: string, opts: { compact?: boolean } = {}) {
  const key = `${currency}|${opts.compact ? "c" : ""}`;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      ...(opts.compact ? { notation: "compact", maximumFractionDigits: 1 } : {}),
    });
    formatters.set(key, f);
  }
  return f.format(centsToDecimalString(cents) as `${number}`);
}
