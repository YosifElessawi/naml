// Barrel: every adapter hook + pure selector slice-13 ships to wire
// slices 3–8's components to the live store. Each downstream component
// merge swaps its fixture import with one of these.

export {
  type DashboardData,
  type SprintRow,
  selectDashboardData,
  useDashboardData,
} from "./useDashboardData.ts";
export {
  type StepperData,
  type AuxStats,
  selectSprintData,
  useSprintData,
} from "./useSprintData.ts";
export {
  type LanesData,
  type LaneTrack,
  selectLanesData,
  useLanesData,
} from "./useLanesData.ts";
export {
  type SliceCardData,
  selectSliceCardData,
  useSliceCardData,
} from "./useSliceCardData.ts";
export {
  type SliceDrawerData,
  selectSliceDrawerData,
  useSliceDrawerData,
} from "./useSliceDrawerData.ts";
export {
  type HealthData,
  selectHealthData,
  useHealthData,
} from "./useHealthData.ts";
