import {
  Field,
  Panel,
  SelectInput,
  TextInput,
} from "../components/widgets.tsx";
import type { AccountConfig } from "../types.ts";

const KNOWN_MODELS = [
  "claude-opus-4-7",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];

export function validateAccount(a: AccountConfig): Partial<Record<keyof AccountConfig, string>> {
  const errs: Partial<Record<keyof AccountConfig, string>> = {};
  if (!a.model.trim()) {
    errs.model = "required";
  } else if (!KNOWN_MODELS.includes(a.model) && !a.modelOptions.includes(a.model)) {
    errs.model = `unknown model; expected one of ${KNOWN_MODELS.join(", ")}`;
  }
  return errs;
}

function fmtTokens(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

export function Account({
  value,
  onChange,
  onCommit,
}: {
  value: AccountConfig;
  onChange: (next: AccountConfig) => void;
  onCommit: () => void;
}) {
  const errors = validateAccount(value);

  function set<K extends keyof AccountConfig>(key: K, v: AccountConfig[K]) {
    onChange({ ...value, [key]: v });
  }

  function commitIfValid() {
    if (Object.keys(errors).length === 0) {
      onCommit();
    }
  }

  return (
    <Panel title="Account · CLAUDE_CONFIG_DIR">
      <Field
        label="Active config dir"
        description="Switches which Claude account naml uses."
        htmlFor="account-config-dir"
      >
        <SelectInput
          id="account-config-dir"
          value={value.configDir}
          options={value.configDirOptions}
          onChange={(v) => {
            set("configDir", v);
            onCommit();
          }}
          width="wide"
        />
      </Field>
      <Field
        label="Model"
        description="Used by lanes when spawning `claude -p`."
        htmlFor="account-model"
        error={errors.model}
      >
        <SelectInput
          id="account-model"
          value={value.model}
          options={value.modelOptions}
          onChange={(v) => {
            set("model", v);
            if (KNOWN_MODELS.includes(v) || value.modelOptions.includes(v)) {
              onCommit();
            }
          }}
        />
      </Field>
      <Field
        label="Context window"
        description="Used to compute slice ctx %. Pulled from model."
      >
        <span className="mono">{fmtTokens(value.contextWindow)} tokens</span>
      </Field>
      <Field
        label="Session token limit"
        description="Plan cap. Empty = no limit."
        htmlFor="account-session-limit"
      >
        <TextInput
          id="account-session-limit"
          value={value.sessionTokenLimit?.toString() ?? ""}
          onChange={(v) => {
            const n = v.trim() === "" ? null : Number.parseInt(v, 10);
            set("sessionTokenLimit", Number.isFinite(n) && (n ?? 0) > 0 ? n : null);
          }}
          onBlur={commitIfValid}
          width="short"
        />
      </Field>
      <Field
        label="Weekly token limit"
        description="Plan cap. Empty = no limit."
        htmlFor="account-weekly-limit"
      >
        <TextInput
          id="account-weekly-limit"
          value={value.weeklyTokenLimit?.toString() ?? ""}
          onChange={(v) => {
            const n = v.trim() === "" ? null : Number.parseInt(v, 10);
            set("weeklyTokenLimit", Number.isFinite(n) && (n ?? 0) > 0 ? n : null);
          }}
          onBlur={commitIfValid}
          width="short"
        />
      </Field>
      <Field
        label="Session reset anchor"
        description="ISO 8601 timestamp at which the session quota resets."
        htmlFor="account-session-reset"
      >
        <TextInput
          id="account-session-reset"
          value={value.sessionResetAt ?? ""}
          onChange={(v) => set("sessionResetAt", v.trim() === "" ? null : v)}
          onBlur={commitIfValid}
        />
      </Field>
      <Field
        label="Weekly reset anchor"
        description="ISO 8601 timestamp at which the weekly quota resets."
        htmlFor="account-weekly-reset"
      >
        <TextInput
          id="account-weekly-reset"
          value={value.weeklyResetAt ?? ""}
          onChange={(v) => set("weeklyResetAt", v.trim() === "" ? null : v)}
          onBlur={commitIfValid}
        />
      </Field>
    </Panel>
  );
}
