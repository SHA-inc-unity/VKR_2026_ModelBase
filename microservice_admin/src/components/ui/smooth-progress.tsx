'use client';

import { useEffect, useRef, useState } from 'react';
import { Progress } from './progress';

/**
 * Smooth, monotonic progress display.
 *
 * Backend dataset jobs report progress in coarse, uneven steps with opaque
 * pauses (overall crawls 5→40 during the kline fetch, then jumps 50→70→85→90
 * →100 while RSI / upsert / feature phases run as single calls). Rendering the
 * raw value makes the bar freeze, then jump. This hook:
 *   • eases the displayed value up to each new target over a few frames, and
 *   • while the job is still running and the real target is stalled below
 *     ~99%, gently trickles upward toward `target + margin` (capped, never
 *     reaching 100) so the bar visibly "breathes" during opaque phases without
 *     lying about completion.
 * The displayed value never moves backward except when `target` itself drops
 * (a new run / reset), where it snaps down immediately.
 */
export function useSmoothPercent(target: number, running: boolean): number {
  const safeTarget = Number.isFinite(target) ? Math.max(0, Math.min(100, target)) : 0;
  const [display, setDisplay] = useState(safeTarget);
  const targetRef = useRef(safeTarget);
  const runningRef = useRef(running);
  targetRef.current = safeTarget;
  runningRef.current = running;

  // Snap downward immediately on reset (e.g. a new download sends target → 0).
  useEffect(() => {
    setDisplay((d) => (safeTarget < d ? safeTarget : d));
  }, [safeTarget]);

  useEffect(() => {
    const id = window.setInterval(() => {
      setDisplay((d) => {
        const tgt = targetRef.current;
        if (d < tgt) {
          // Ease toward the real target (≈ a third of the gap per tick, min 0.6).
          return Math.min(tgt, d + Math.max(0.6, (tgt - d) * 0.34));
        }
        if (runningRef.current && tgt < 99) {
          // Trickle during opaque phases, but stay honest: cap close to target.
          const cap = Math.min(99, tgt + 10);
          if (d < cap) return Math.min(cap, d + 0.4);
        }
        // Returning the same value makes React bail out — no wasted re-render.
        return d;
      });
    }, 300);
    return () => window.clearInterval(id);
  }, []);

  return Math.round(display);
}

/**
 * Drop-in replacement for {@link Progress} that animates/trickles the value
 * via {@link useSmoothPercent}. Pass `running` so it knows whether to trickle
 * during opaque backend phases.
 */
export function SmoothProgress({
  value,
  running,
  className,
}: {
  value: number;
  running: boolean;
  className?: string;
}): JSX.Element {
  const smooth = useSmoothPercent(value, running);
  return <Progress value={smooth} className={className} />;
}
