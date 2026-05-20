// React hook that wraps ``intervene`` with the flash/pending/error state the
// drawer needs to render. Pulled out of intervene.ts so the fetch wrapper
// stays React-free (re-usable from store subscribers, tests, future menu-bar
// surface, etc.).
//
// Usage from slice-7's drawer (or App.tsx):
//
//   const { run, pending, lastResult } = useIntervene(sliceId);
//   <button disabled={pending} onClick={() => run("hold")}>HOLD</button>
//   {lastResult?.ok === false && <span>{lastResult.error}</span>}

import { useCallback, useState } from "react";

import { type InterveneAction, type InterveneResult, intervene } from "./intervene.ts";

export interface UseInterveneApi {
  /** Trigger an intervention. The promise resolves to ``InterveneResult``
   *  regardless of success/failure — the hook never throws. */
  run: (action: InterveneAction) => Promise<InterveneResult>;
  /** True while a request is in flight. The drawer should disable buttons
   *  for the duration so a double-click can't fire HOLD twice. */
  pending: boolean;
  /** The most recent result; ``null`` before the first call. Drawer reads
   *  this to render the flash bar text and apply the "Held · terminal
   *  unlocked" / "Hold requested · waiting for current turn" copy. */
  lastResult: InterveneResult | null;
  /** Convenience for "clear the flash bar after the user dismisses it". */
  clear: () => void;
}

export function useIntervene(sliceId: string): UseInterveneApi {
  const [pending, setPending] = useState(false);
  const [lastResult, setLastResult] = useState<InterveneResult | null>(null);

  const run = useCallback(
    async (action: InterveneAction): Promise<InterveneResult> => {
      setPending(true);
      const result = await intervene(sliceId, action);
      setLastResult(result);
      setPending(false);
      return result;
    },
    [sliceId],
  );

  const clear = useCallback(() => setLastResult(null), []);

  return { run, pending, lastResult, clear };
}
