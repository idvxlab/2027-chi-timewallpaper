/**
 * Pure helpers extracted from useSubjectLift for testability.
 *
 * These functions capture the *decision logic* of the lift state machine
 * without any React, DOM, or side-effects. They exist purely so we can
 * unit-test the race-B fix (pointerup during extracting must NOT abort
 * the in-flight extraction) without spinning up a full React renderer.
 *
 * Do NOT add business state here — these helpers only mirror what the
 * hook already does.
 */

import type { LiftState } from "./useSubjectLift";
import type { SubjectRegion } from "../subjectGeometry";

export type PointerUpDecision =
  | { kind: "cancel_idle" } // was "pressing"; short press before timer
  | { kind: "release_during_extracting"; region: SubjectRegion } // race B: keep fetch alive
  | { kind: "mark_release_done" } // was "waiting_for_release"; enter armed_lifted
  | { kind: "noop_armed" } // already armed_lifted (second tap is handled by layer)
  | { kind: "noop_releasing" }
  | { kind: "noop_idle" };

/**
 * Decide what `onPointerUp` should do given the current lift state.
 *
 * History: prior to this fix, the "extracting" branch called
 * `cancelPress() + setState(idle)` which aborted the in-flight cutout
 * request. Users naturally release the press long before the backend
 * finishes (~10–30s for matting), so this caused the cutout to never
 * arrive at `armed_lifted`. The fix is to keep the fetch alive and
 * mark `firstReleaseDoneRef = true`; `runExtraction` will then promote
 * to `armed_lifted` as soon as the API succeeds.
 */
export function decidePointerUp(state: LiftState): PointerUpDecision {
  switch (state.status) {
    case "pressing":
      return { kind: "cancel_idle" };
    case "extracting":
      return { kind: "release_during_extracting", region: state.region };
    case "waiting_for_release":
      return { kind: "mark_release_done" };
    case "armed_lifted":
      return { kind: "noop_armed" };
    case "recording":
      // The hook doesn't explicitly handle recording in pointerup;
      // falls through to the implicit "noop" branch.
      return { kind: "noop_armed" };
    case "releasing":
      return { kind: "noop_releasing" };
    case "idle":
      return { kind: "noop_idle" };
    case "error":
      return { kind: "noop_idle" };
  }
}