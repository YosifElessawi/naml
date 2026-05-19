import { useCallback, useEffect, useRef, useState } from "react";
import { saveSettings } from "./config-client.ts";
import type { NamlSettings } from "./types.ts";

/**
 * Autosave hook for the Settings page. Returns the current draft, a
 * mutator that updates the draft, an explicit `flush()` that ships the
 * pending diff to the server (call on field blur), and the timestamp
 * of the most recent successful save so the UI can render a
 * `Saved 2s ago` badge.
 *
 * When `initial` changes (e.g. the async load resolves after first
 * render), the draft re-syncs — but only if the user hasn't introduced
 * pending edits yet. This avoids clobbering in-flight user input on
 * a late load.
 */
export function useAutosave(initial: NamlSettings) {
  const [draft, setDraft] = useState<NamlSettings>(initial);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [pending, setPending] = useState<Partial<NamlSettings>>({});
  const hasEdited = useRef(false);

  useEffect(() => {
    if (!hasEdited.current) {
      setDraft(initial);
    }
  }, [initial]);

  const updateSection = useCallback(
    <K extends keyof NamlSettings>(key: K, value: NamlSettings[K]) => {
      hasEdited.current = true;
      setDraft((prev) => ({ ...prev, [key]: value }));
      setPending((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const flush = useCallback(async () => {
    if (Object.keys(pending).length === 0) {
      return;
    }
    const ok = await saveSettings(pending);
    if (ok) {
      setSavedAt(Date.now());
      setPending({});
    }
  }, [pending]);

  return { draft, updateSection, flush, savedAt };
}
