/**
 * Money is integer minor units end to end. These helpers never go through floats:
 * parsing is string based and formatting hands Intl an exact decimal string.
 * Minor-unit exponents mirror the backend's format_money (notifications/formatting.py).
 */

export type ParseResult = { ok: true; cents: number } | { ok: false; error: string };

const EXPONENTS: Readonly<Record<string, number>> = {
  JPY: 0,
  KRW: 0,
  BHD: 3,
  IQD: 3,
  JOD: 3,
  KWD: 3,
  LYD: 3,
  OMR: 3,
  TND: 3,
};

/** Number of minor-unit digits for an ISO 4217 code: 0 (JPY), 3 (KWD), else 2. */
export function currencyExponent(currency: string): number {
  return EXPONENTS[currency.toUpperCase()] ?? 2;
}

/** "1,234.5" USD -> 123450; "500" JPY -> 500; "1.234" KWD -> 1234. Rejects negatives,
 * too many decimals for the currency, exponents, and unsafe integers. */
export function parseAmountToCents(input: string, currency: string): ParseResult {
  const exp = currencyExponent(currency);
  const s = input.trim().replace(/^\$\s*/, "");
  if (s === "" || s === ".") return { ok: false, error: "Enter an amount" };
  if (s.startsWith("-")) return { ok: false, error: "Amount must be positive" };
  const m = /^(\d{1,3}(?:,\d{3})+|\d+)?(?:\.(\d*))?$/.exec(s);
  if (!m || (m[1] === undefined && !m[2])) return { ok: false, error: "Enter a valid amount, e.g. 125.50" };
  const fracDigits = m[2] ?? "";
  if (fracDigits.length > exp) {
    return {
      ok: false,
      error: exp === 0 ? `${currency.toUpperCase()} has no decimal places` : `At most ${exp} decimal places`,
    };
  }
  const whole = (m[1] ?? "0").replace(/,/g, "");
  const frac = fracDigits.padEnd(exp, "0");
  const cents = BigInt(whole) * 10n ** BigInt(exp) + BigInt(frac || "0");
  if (cents <= 0n) return { ok: false, error: "Amount must be greater than 0" };
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, error: "Amount is too large" };
  return { ok: true, cents: Number(cents) };
}

/** 123450 USD -> "1234.50", 500 JPY -> "500", 1234 KWD -> "1.234" (exact; no separators). */
export function centsToDecimalString(cents: number, currency: string): string {
  if (!Number.isSafeInteger(cents)) throw new RangeError("cents must be a safe integer");
  const exp = currencyExponent(currency);
  const neg = cents < 0;
  const abs = BigInt(Math.abs(cents));
  const unit = 10n ** BigInt(exp);
  const whole = abs / unit;
  const frac = exp === 0 ? "" : `.${(abs % unit).toString().padStart(exp, "0")}`;
  return `${neg ? "-" : ""}${whole}${frac}`;
}

const formatters = new Map<string, Intl.NumberFormat>();

export function formatCents(cents: number, currency: string, opts: { compact?: boolean } = {}) {
  const exp = currencyExponent(currency);
  const key = `${currency}|${opts.compact ? "c" : ""}`;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      ...(opts.compact
        ? { notation: "compact", maximumFractionDigits: 1 }
        : { minimumFractionDigits: exp, maximumFractionDigits: exp }),
    });
    formatters.set(key, f);
  }
  return f.format(centsToDecimalString(cents, currency) as `${number}`);
}
