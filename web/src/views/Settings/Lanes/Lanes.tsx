import {
  Field,
  Muted,
  Panel,
  Slider,
  SliderValue,
  Toggle,
} from "../components/widgets.tsx";
import type { LanesConfig } from "../types.ts";

const LANE_MIN = 1;
const LANE_MAX = 8;

export function Lanes({
  value,
  onChange,
  onCommit,
}: {
  value: LanesConfig;
  onChange: (next: LanesConfig) => void;
  onCommit: () => void;
}) {
  function set<K extends keyof LanesConfig>(key: K, v: LanesConfig[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <Panel title="Lanes">
      <Field
        label="Default parallel lanes"
        description="Capped by DAG width + headroom at runtime."
        htmlFor="lanes-default"
      >
        <Slider
          id="lanes-default"
          ariaLabel="default parallel lanes"
          value={value.defaultLanes}
          min={LANE_MIN}
          max={LANE_MAX}
          step={1}
          onChange={(v) => {
            // Snap to integers and clamp defensively.
            const snapped = Math.max(LANE_MIN, Math.min(LANE_MAX, Math.round(v)));
            onChange({
              ...value,
              defaultLanes: snapped,
              // hard cap must never sit below default — lift it if needed.
              hardCap: Math.max(value.hardCap, snapped),
            });
          }}
          onBlur={onCommit}
        />
        <SliderValue value={value.defaultLanes} />
        <Muted>range 1–{LANE_MAX}</Muted>
      </Field>
      <Field
        label="DAG width detection"
        description="Runtime cap won't exceed how many slices can actually run in parallel."
      >
        <Toggle
          ariaLabel="DAG width detection"
          checked={value.dagWidthDetection}
          onChange={(v) => {
            set("dagWidthDetection", v);
            onCommit();
          }}
        />
        <Muted>{value.dagWidthDetection ? "on (recommended)" : "off"}</Muted>
      </Field>
      <Field
        label="Hard cap"
        description="Absolute ceiling. Default 8; never exceeded even if DAG width is higher."
        htmlFor="lanes-hardcap"
      >
        <Slider
          id="lanes-hardcap"
          ariaLabel="hard cap"
          value={value.hardCap}
          min={value.defaultLanes}
          max={LANE_MAX}
          step={1}
          onChange={(v) => {
            const snapped = Math.max(
              value.defaultLanes,
              Math.min(LANE_MAX, Math.round(v)),
            );
            set("hardCap", snapped);
          }}
          onBlur={onCommit}
        />
        <SliderValue value={value.hardCap} />
      </Field>
    </Panel>
  );
}
