// REST wiring for the slice-drawer's HOLD / RESUME / MARK_FAILED / SKIP /
// OPEN_TERMINAL buttons. Pairs with naml/server.py:_intervene_response.
//
// Slice-7 built the drawer with an ``onIntervene`` prop that takes an action
// and returns a promise of ``InterveneResult``; slice-14 provides this
// fetch wrapper as the real wiring. Mount it on the drawer at the App
// level (e.g. ``<SliceDrawer onIntervene={intervene} ... />``).

export type InterveneAction = "hold" | "resume" | "mark-failed" | "skip" | "open-terminal";

export interface InterveneSuccess {
  ok: true;
  sliceId: string;
  action: InterveneAction;
  detail?: string;
  currentState?: string;
  worktree?: string;
}

export interface InterveneFailure {
  ok: false;
  sliceId: string;
  action: InterveneAction;
  status: number;
  /** Human-readable, safe to surface inline in the drawer flash bar. */
  error: string;
  /** When the server returned 409 with the current state, the drawer can
   *  re-render that pill while the flash is visible. */
  currentState?: string;
}

export type InterveneResult = InterveneSuccess | InterveneFailure;

interface ServerResponseShape {
  ok?: boolean;
  error?: string;
  detail?: string;
  current_state?: string;
  worktree?: string;
}

/**
 * POST /intervene/{sliceId}?action=<action>.
 *
 * Never throws — even network failures resolve as ``InterveneFailure`` so
 * the drawer's flash bar always gets actionable text. The server is the
 * source of truth for whether an action is legal (e.g. open-terminal in a
 * locked state returns 409); we mirror that here for the UI to render.
 */
export async function intervene(
  sliceId: string,
  action: InterveneAction,
  init?: { fetch?: typeof fetch; signal?: AbortSignal },
): Promise<InterveneResult> {
  const fetchImpl = init?.fetch ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    return {
      ok: false,
      sliceId,
      action,
      status: 0,
      error: "fetch is not available in this environment",
    };
  }
  const url = `/intervene/${encodeURIComponent(sliceId)}?action=${encodeURIComponent(action)}`;
  let response: Response;
  try {
    response = await fetchImpl(url, { method: "POST", signal: init?.signal });
  } catch (err) {
    return {
      ok: false,
      sliceId,
      action,
      status: 0,
      error: err instanceof Error ? err.message : "network error",
    };
  }

  let body: ServerResponseShape | null = null;
  try {
    body = (await response.json()) as ServerResponseShape;
  } catch {
    body = null;
  }

  if (!response.ok) {
    return {
      ok: false,
      sliceId,
      action,
      status: response.status,
      error: body?.error ?? `intervention failed with HTTP ${response.status}`,
      currentState: body?.current_state,
    };
  }
  return {
    ok: true,
    sliceId,
    action,
    detail: body?.detail,
    currentState: body?.current_state,
    worktree: body?.worktree,
  };
}

// Mirror of ``naml.states.TERMINAL_UNLOCKED_STATES`` so the drawer can
// answer "is the open-terminal button enabled?" without a network round
// trip. Keep these in lockstep if more states are added.
export const TERMINAL_UNLOCKED_STATES: ReadonlySet<string> = new Set([
  "held",
  "review",
  "review_passed",
  "merged",
  "failed",
  "needs_human_review",
  "abandoned",
  "blocked_upstream",
]);

export function isTerminalUnlocked(state: string): boolean {
  return TERMINAL_UNLOCKED_STATES.has(state);
}
