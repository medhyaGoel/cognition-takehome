import { describe, expect, it } from "vitest";
import { centsToDecimalString, currencyExponent, formatCents, parseAmountToCents } from "./money";

const ok = (s: string, currency = "USD") => {
  const r = parseAmountToCents(s, currency);
  if (!r.ok) throw new Error(`expected ok for ${s}: ${r.error}`);
  return r.cents;
};
const bad = (s: string, currency = "USD") => {
  const r = parseAmountToCents(s, currency);
  expect(r.ok, `expected error for ${JSON.stringify(s)}`).toBe(false);
};

describe("parseAmountToCents", () => {
  it("parses whole and fractional amounts exactly", () => {
    expect(ok("12")).toBe(1200);
    expect(ok("12.3")).toBe(1230);
    expect(ok("12.34")).toBe(1234);
    expect(ok("0.01")).toBe(1);
    expect(ok(".5")).toBe(50);
    expect(ok("12.")).toBe(1200);
    expect(ok("  $1,234.56 ")).toBe(123456);
    expect(ok("1,000,000")).toBe(100000000);
  });

  it("avoids float rounding traps", () => {
    // parseFloat("0.29") * 100 === 28.999999999999996; 1.005*100 === 100.49999999999999
    expect(ok("0.29")).toBe(29);
    expect(ok("1.15")).toBe(115);
    expect(ok("4.35")).toBe(435);
    expect(ok("10.13")).toBe(1013);
    expect(ok("90071992547409.91")).toBe(9007199254740991);
  });

  it("rejects malformed, negative, zero and over-precise input", () => {
    for (const s of ["", " ", ".", "-1", "-0.50", "0", "0.00", "1.234", "1e3", "abc", "1.2.3", "1,23", "12,34.5", "$", "+5", "0x10", "١٢"]) {
      bad(s);
    }
  });

  it("rejects amounts beyond Number.MAX_SAFE_INTEGER cents", () => {
    bad("90071992547409.92");
    bad("999999999999999999");
  });
});

describe("formatting", () => {
  it("round-trips cents to a decimal string without floats", () => {
    expect(centsToDecimalString(0, "USD")).toBe("0.00");
    expect(centsToDecimalString(5, "USD")).toBe("0.05");
    expect(centsToDecimalString(123456, "USD")).toBe("1234.56");
    expect(centsToDecimalString(-250, "USD")).toBe("-2.50");
    expect(centsToDecimalString(9007199254740991, "USD")).toBe("90071992547409.91");
  });

  it("formats currency", () => {
    expect(formatCents(123456, "USD")).toBe("$1,234.56");
    expect(formatCents(1013, "EUR")).toBe("€10.13");
    expect(formatCents(9007199254740991, "USD")).toBe("$90,071,992,547,409.91");
  });
});

describe("currency exponents (mirror backend format_money)", () => {
  it("maps ISO codes to minor-unit digits", () => {
    for (const c of ["JPY", "KRW"]) expect(currencyExponent(c)).toBe(0);
    for (const c of ["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"]) expect(currencyExponent(c)).toBe(3);
    for (const c of ["USD", "EUR", "GBP", "usd"]) expect(currencyExponent(c)).toBe(2);
  });

  it("0 decimals (JPY, KRW): whole units only", () => {
    expect(ok("500", "JPY")).toBe(500);
    expect(ok("1,234", "KRW")).toBe(1234);
    bad("5.5", "JPY");
    bad("5.00", "KRW");
    expect(centsToDecimalString(500, "JPY")).toBe("500");
    expect(formatCents(123456, "JPY")).toBe("¥123,456");
  });

  it("3 decimals (BHD, IQD, JOD, KWD, LYD, OMR, TND)", () => {
    for (const c of ["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"]) {
      expect(ok("1.234", c)).toBe(1234);
      expect(ok("2.5", c)).toBe(2500);
      bad("1.2345", c);
      expect(centsToDecimalString(1234, c)).toBe("1.234");
      expect(centsToDecimalString(5, c)).toBe("0.005");
    }
    expect(formatCents(1234, "KWD")).toMatch(/^KWD\s1\.234$/);
    expect(formatCents(1234, "IQD")).toMatch(/^IQD\s1\.234$/);
  });

  it("2 decimals otherwise", () => {
    expect(ok("1.23", "EUR")).toBe(123);
    bad("1.234", "EUR");
    expect(centsToDecimalString(123, "GBP")).toBe("1.23");
  });
});
