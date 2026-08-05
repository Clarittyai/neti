"use client";

/**
 * Stage — a fixed-geometry scene that SCALES to its container instead of clipping.
 *
 * Scenes are authored in a comfortable coordinate space (e.g. 340×168) with absolutely-positioned
 * figures. On a narrow phone that space is wider than the column it sits in, and without this the
 * composition is simply cropped. Stage measures the real width and scales the whole thing down, so
 * nothing is ever cut off — which is why DESIGN.md's pre-flight list says 375px.
 *
 * It never scales UP past `maxScale` (default 1): a small composition blown up to fill a desktop
 * column looks like clip-art.
 *
 * Ported from clarity-platform. See DESIGN.md for why it is copied rather than imported.
 */
import { useRef } from "react";

import { useMeasuredWidth } from "./kernel";

export function Stage({
  width,
  height,
  maxScale = 1,
  className,
  children,
}: {
  /** Design-space width the children are positioned in. */
  width: number;
  /** Design-space height. */
  height: number;
  maxScale?: number;
  className?: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const measured = useMeasuredWidth(ref);
  const scale = measured > 0 ? Math.min(maxScale, measured / width) : maxScale;

  return (
    <div ref={ref} className={className} style={{ height: height * scale }}>
      <div
        style={{
          width,
          height,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
          position: "relative",
        }}
      >
        {children}
      </div>
    </div>
  );
}
