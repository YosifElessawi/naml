// React-friendly wrapper that FLIP-animates its keyed children on every
// commit. Drop in around any list whose item order is driven by state and
// you'll get the "card re-order: FLIP 300ms ease-out" Q8 smoothness row,
// for free.
//
// Usage (downstream wiring slice-5 will pick up at merge time):
//
//     <FlipList>
//       {laneCards.map(card =>
//         <div data-slice-id={card.id} key={card.id}>…</div>
//       )}
//     </FlipList>
//
// The key extractor defaults to `el.dataset.sliceId`; pass a different
// `keyAttr` if the list keys on something else (e.g. sprint id). The list
// element itself is a `<div>` by default; override via the `as` prop.

import { Children, type ReactNode, useLayoutEffect, useRef } from "react";

import { Flip, type RectMap } from "../../lib/flip.ts";

interface FlipListProps {
  children: ReactNode;
  /** Tag for the wrapping element. Default `"ul"`. */
  as?: "ul" | "ol" | "div";
  /** dataset attribute used to identify each child. Default `"sliceId"`. */
  keyAttr?: string;
  /** className passed through to the wrapping element. */
  className?: string;
  /** Optional ARIA label for the list container. */
  ariaLabel?: string;
  /** Override the Flip instance — used by tests to spy on play(). */
  flip?: Flip;
}

export function FlipList({
  children,
  as = "ul",
  keyAttr = "sliceId",
  className,
  ariaLabel,
  flip,
}: FlipListProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const flipRef = useRef<Flip>(flip ?? new Flip((el) => el.dataset[keyAttr] as string | undefined));
  // Holds the rect snapshot from the *previous* commit. The first commit
  // sees an empty map (so no inverse-translate fires); every subsequent
  // commit animates from the prior layout to the new one.
  const prevRectsRef = useRef<RectMap>(new Map());

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    // After this commit's DOM is in place, animate from the rects we
    // captured on the previous commit. Then snapshot the just-committed
    // positions so the next commit has something to invert from.
    flipRef.current.playFromSnapshot(root, prevRectsRef.current);
    prevRectsRef.current = flipRef.current.snapshot(root);
  });

  // Honour `as` without conditional rendering at the call site.
  const setRef = (el: HTMLElement | null) => {
    rootRef.current = el;
  };
  if (as === "ol") {
    return (
      <ol
        ref={setRef as React.RefCallback<HTMLOListElement>}
        className={className}
        aria-label={ariaLabel}
      >
        {Children.toArray(children)}
      </ol>
    );
  }
  if (as === "div") {
    return (
      <div
        ref={setRef as React.RefCallback<HTMLDivElement>}
        className={className}
        aria-label={ariaLabel}
      >
        {Children.toArray(children)}
      </div>
    );
  }
  return (
    <ul
      ref={setRef as React.RefCallback<HTMLUListElement>}
      className={className}
      aria-label={ariaLabel}
    >
      {Children.toArray(children)}
    </ul>
  );
}
