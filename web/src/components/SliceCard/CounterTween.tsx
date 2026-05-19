import { useEffect, useRef, useState } from "react";

interface CounterTweenProps {
  value: number;
  format?: (n: number) => string;
  durationMs?: number;
  className?: string;
}

// Tweens a numeric value over ~400ms (default) using requestAnimationFrame.
// Respects prefers-reduced-motion by snapping to the final value.
export function CounterTween({ value, format, durationMs = 400, className }: CounterTweenProps) {
  const [display, setDisplay] = useState(value);
  const displayRef = useRef(value);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    displayRef.current = display;
  }, [display]);

  useEffect(() => {
    const reducedMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) {
      setDisplay(value);
      return;
    }
    const startValue = displayRef.current;
    let startTime: number | null = null;

    const step = (ts: number) => {
      if (startTime == null) startTime = ts;
      const elapsed = ts - startTime;
      const t = Math.min(1, elapsed / durationMs);
      const eased = 1 - (1 - t) * (1 - t);
      const next = startValue + (value - startValue) * eased;
      setDisplay(next);
      if (t < 1) {
        frameRef.current = requestAnimationFrame(step);
      }
    };
    frameRef.current = requestAnimationFrame(step);

    return () => {
      if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    };
  }, [value, durationMs]);

  return <span className={className}>{format ? format(display) : String(display)}</span>;
}
