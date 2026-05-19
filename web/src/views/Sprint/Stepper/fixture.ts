import type { StepperData } from "./types.ts";

export const stepperFixture: StepperData = {
  currentState: "executing",
  durations: {
    planning: "12s",
    publishing: "4s",
    executing: "21m · ETA 14m",
    awaiting_signoff: "—",
    merging: "—",
    complete: "—",
  },
  sliceStates: ["merged", "merged", "review", "work", "work", "idle", "idle", "idle"],
  aux: {
    elapsed: "21m 14s",
    eta: "~14m",
    cost: "$2.41",
    projTotal: "$18.42",
    lanes: "3 / 3",
    slices: "5 / 8",
    retries: "0",
    tier1: "60%",
    lgtm: "2",
    lastTick: "2s ago",
  },
  hitExits: [],
};
