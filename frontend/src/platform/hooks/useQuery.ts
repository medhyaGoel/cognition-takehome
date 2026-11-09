import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/errors";

export interface QueryState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  refetch: () => Promise<void>;
}

/** Minimal data hook: latest request wins, aborts on unmount/deps change. */
export function useQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): QueryState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const run = useCallback(async (signal: AbortSignal) => {
    const id = ++seq.current;
    setLoading(true);
    try {
      const result = await fetcherRef.current(signal);
      if (id !== seq.current) return;
      setData(result);
      setError(undefined);
    } catch (err) {
      if (id !== seq.current || signal.aborted) return;
      setError(err instanceof ApiError ? err : new ApiError(0, "client_error", String(err)));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    // Fetch-on-mount: setting `loading` here is the point of the hook.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void run(ctrl.signal);
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const refetch = useCallback(() => run(new AbortController().signal), [run]);
  return { data, error, loading, refetch };
}
