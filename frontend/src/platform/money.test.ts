import { describe, expect, it } from "vitest";
import { centsToDecimalString, formatCents, parseAmountToCents } from "./money";

const ok = (s: string) => {
  const r = parseAmountToCents(s);
  if (!r.ok) throw new Error(`expected ok for ${s}: ${r.error}`);
  return r.cents;
};
const bad = (s: string) => {
  const r = parseAmountToCents(s);
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
    expect(centsToDecimalString(0)).toBe("0.00");
    expect(centsToDecimalString(5)).toBe("0.05");
    expect(centsToDecimalString(123456)).toBe("1234.56");
    expect(centsToDecimalString(-250)).toBe("-2.50");
    expect(centsToDecimalString(9007199254740991)).toBe("90071992547409.91");
  });

  it("formats currency", () => {
    expect(formatCents(123456, "USD")).toBe("$1,234.56");
    expect(formatCents(1013, "EUR")).toBe("€10.13");
    expect(formatCents(9007199254740991, "USD")).toBe("$90,071,992,547,409.91");
  });
});
