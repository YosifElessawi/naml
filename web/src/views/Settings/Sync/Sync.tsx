import {
  Field,
  Muted,
  Panel,
  TextInput,
  Toggle,
  Unit,
} from "../components/widgets.tsx";
import type { SyncConfig } from "../types.ts";

export function validateSync(s: SyncConfig): Partial<Record<keyof SyncConfig, string>> {
  const errs: Partial<Record<keyof SyncConfig, string>> = {};
  if (s.heartbeatSeconds <= 0) {
    errs.heartbeatSeconds = "must be > 0";
  }
  if (s.slowThresholdSeconds <= s.heartbeatSeconds) {
    errs.slowThresholdSeconds = "must exceed heartbeat";
  }
  if (s.lostThresholdSeconds <= s.slowThresholdSeconds) {
    errs.lostThresholdSeconds = "must exceed SLOW threshold";
  }
  return errs;
}

function num(v: string, fallback: number): number {
  const n = Number.parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

export function Sync({
  value,
  onChange,
  onCommit,
}: {
  value: SyncConfig;
  onChange: (next: SyncConfig) => void;
  onCommit: () => void;
}) {
  const errors = validateSync(value);

  function set<K extends keyof SyncConfig>(key: K, v: SyncConfig[K]) {
    onChange({ ...value, [key]: v });
  }

  function commitIfValid() {
    if (Object.keys(errors).length === 0) {
      onCommit();
    }
  }

  return (
    <Panel title="Sync · SSE heartbeat">
      <Field
        label="SSE heartbeat"
        description="Drives the `Synced Ns ago` badge."
        htmlFor="sync-heartbeat"
        error={errors.heartbeatSeconds}
      >
        <TextInput
          id="sync-heartbeat"
          value={value.heartbeatSeconds.toString()}
          onChange={(v) => set("heartbeatSeconds", num(v, value.heartbeatSeconds))}
          onBlur={commitIfValid}
          width="short"
          invalid={Boolean(errors.heartbeatSeconds)}
        />
        <Unit>seconds</Unit>
      </Field>
      <Field
        label="SLOW threshold"
        description="If no event for this long, switch sync dot to amber."
        htmlFor="sync-slow"
        error={errors.slowThresholdSeconds}
      >
        <TextInput
          id="sync-slow"
          value={value.slowThresholdSeconds.toString()}
          onChange={(v) =>
            set("slowThresholdSeconds", num(v, value.slowThresholdSeconds))
          }
          onBlur={commitIfValid}
          width="short"
          invalid={Boolean(errors.slowThresholdSeconds)}
        />
        <Unit>seconds</Unit>
      </Field>
      <Field
        label="LOST threshold"
        description="After this long, switch sync dot to red."
        htmlFor="sync-lost"
        error={errors.lostThresholdSeconds}
      >
        <TextInput
          id="sync-lost"
          value={value.lostThresholdSeconds.toString()}
          onChange={(v) =>
            set("lostThresholdSeconds", num(v, value.lostThresholdSeconds))
          }
          onBlur={commitIfValid}
          width="short"
          invalid={Boolean(errors.lostThresholdSeconds)}
        />
        <Unit>seconds</Unit>
      </Field>
      <Field
        label="Reduce motion"
        description="Disables card-flip + counter-tween animations."
      >
        <Toggle
          ariaLabel="reduce motion"
          checked={value.reduceMotion}
          onChange={(v) => {
            set("reduceMotion", v);
            onCommit();
          }}
        />
        <Muted>{value.reduceMotion ? "on (calm)" : "off (smooth)"}</Muted>
      </Field>
    </Panel>
  );
}
