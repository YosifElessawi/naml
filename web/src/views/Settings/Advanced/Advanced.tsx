import { useState } from "react";
import { resetAggregates } from "../config-client.ts";
import {
  Button,
  Field,
  Muted,
  Panel,
  Toggle,
  widgetCss as css,
} from "../components/widgets.tsx";
import type { AdvancedConfig } from "../types.ts";

type ConfirmKind = "reset-aggregates" | "wipe-history" | null;

export function Advanced({
  value,
  onChange,
  onCommit,
}: {
  value: AdvancedConfig;
  onChange: (next: AdvancedConfig) => void;
  onCommit: () => void;
}) {
  const [confirming, setConfirming] = useState<ConfirmKind>(null);
  const [resetState, setResetState] = useState<"idle" | "ok" | "fail">("idle");

  function setFlag(name: string, v: boolean) {
    onChange({
      ...value,
      featureFlags: { ...value.featureFlags, [name]: v },
    });
    onCommit();
  }

  function setRaw(raw: string) {
    onChange({ ...value, rawConfigToml: raw });
  }

  async function doReset() {
    const ok = await resetAggregates();
    setResetState(ok ? "ok" : "fail");
    setConfirming(null);
  }

  return (
    <>
      <Panel title="Feature flags">
        {Object.entries(value.featureFlags).map(([name, on]) => (
          <Field key={name} label={name}>
            <Toggle
              ariaLabel={`feature flag ${name}`}
              checked={on}
              onChange={(v) => setFlag(name, v)}
            />
            <Muted>{on ? "enabled" : "disabled"}</Muted>
          </Field>
        ))}
      </Panel>

      <Panel title="Maintenance">
        <Field
          label="JSONL compaction"
          description="Rolls per-slice token JSONL into a compacted parquet-like artifact. Manual trigger; deferred to v2."
        >
          <Button disabled>Run compaction (deferred)</Button>
        </Field>
        <Field
          label="Reset aggregates"
          description="Clears in-memory cost / token rollups. Source-of-truth JSONL is preserved; next cold-start replays."
        >
          {confirming === "reset-aggregates" ? (
            <div className={css.confirmRow}>
              <span className={css.confirmMsg}>
                Aggregates will be cleared. JSONL is untouched. Proceed?
              </span>
              <Button variant="danger" onClick={doReset}>
                Confirm reset
              </Button>
              <Button onClick={() => setConfirming(null)}>Cancel</Button>
            </div>
          ) : (
            <>
              <Button onClick={() => setConfirming("reset-aggregates")}>
                Reset aggregates
              </Button>
              {resetState === "ok" ? (
                <Muted>cleared just now</Muted>
              ) : resetState === "fail" ? (
                <span role="alert" className={css.errorText}>
                  reset failed
                </span>
              ) : null}
            </>
          )}
        </Field>
        <Field
          label="Wipe sprint history"
          description="Destructive — removes .naml/sprints/* state files. Requires double confirmation."
        >
          {confirming === "wipe-history" ? (
            <div className={css.confirmRow}>
              <span className={css.confirmMsg}>
                This deletes all sprint state on disk and cannot be undone.
              </span>
              <Button
                variant="danger"
                onClick={() => {
                  setConfirming(null);
                  // Wire-up to a server endpoint is intentionally deferred —
                  // destructive action ships behind real auth in a later slice.
                }}
              >
                Confirm wipe
              </Button>
              <Button onClick={() => setConfirming(null)}>Cancel</Button>
            </div>
          ) : (
            <Button variant="danger" onClick={() => setConfirming("wipe-history")}>
              Wipe sprint history…
            </Button>
          )}
        </Field>
      </Panel>

      <Panel title="Raw config.toml">
        <div style={{ padding: "12px 18px" }}>
          <textarea
            className={css.textarea}
            aria-label="raw config.toml"
            spellCheck={false}
            value={value.rawConfigToml}
            placeholder="# paste-in only; saving here writes through .naml/config.toml atomically"
            onChange={(e) => setRaw(e.target.value)}
            onBlur={onCommit}
          />
        </div>
      </Panel>
    </>
  );
}
