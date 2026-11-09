import { useCallback, useRef, useState } from "react";
import { ApiError } from "../api/errors";

export type KeyFactory = () => string;
const defaultKey: KeyFactory = () => crypto.randomUUID();

/**
 * One Idempotency-Key per logical submission.
 * - The key is created on the first attempt and REUSED while the outcome is unknown
 *   (network error / 5xx), so a retry can never create a second record.
 * - It is discarded once the server gives a definitive answer (2xx or 4xx).
 * - Concurrent calls (double click, Enter + click) are dropped synchronously.
 */
export function useIdempotentSubmit<T>(
  send: (idempotencyKey: string) => Promise<T>,
  newKey: KeyFactory = defaultKey,
) {
  const keyRef = useRef<string | null>(null);
  const inFlight = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const submit = useCallback(async (): Promise<T | undefined> => {
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setSubmitting(true);
    keyRef.current ??= newKey();
    try {
      const result = await send(keyRef.current);
      keyRef.current = null;
      setRetrying(false);
      return result;
    } catch (err) {
      const unknownOutcome = err instanceof ApiError && err.outcomeUnknown;
      if (!unknownOutcome) keyRef.current = null;
      setRetrying(unknownOutcome);
      throw err;
    } finally {
      inFlight.current = false;
      setSubmitting(false);
    }
  }, [send, newKey]);

  const reset = useCallback(() => {
    keyRef.current = null;
    setRetrying(false);
  }, []);

  return { submit, submitting, retrying, reset };
}
