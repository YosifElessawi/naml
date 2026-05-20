import { useCallback, useEffect, useState } from "react";

const PARAM = "drawer";

function readUrl(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  return params.get(PARAM);
}

function writeUrl(sliceId: string | null) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (sliceId) {
    url.searchParams.set(PARAM, sliceId);
  } else {
    url.searchParams.delete(PARAM);
  }
  window.history.replaceState(null, "", url.toString());
}

/**
 * Drawer state lives in the URL (`?drawer=<slice-id>`) so a refresh keeps
 * it open. Returns the current id, an opener, and a closer.
 */
export function useDrawerUrl() {
  const [sliceId, setSliceId] = useState<string | null>(() => readUrl());

  // Sync on back/forward navigation.
  useEffect(() => {
    const onPop = () => setSliceId(readUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const open = useCallback((id: string) => {
    writeUrl(id);
    setSliceId(id);
  }, []);

  const close = useCallback(() => {
    writeUrl(null);
    setSliceId(null);
  }, []);

  return { sliceId, open, close };
}
