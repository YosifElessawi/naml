import { Field, Panel, TagList, TextInput } from "../components/widgets.tsx";
import type { ProjectConfig } from "../types.ts";

const SLUG_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

export function validateProject(p: ProjectConfig): Partial<Record<keyof ProjectConfig, string>> {
  const errs: Partial<Record<keyof ProjectConfig, string>> = {};
  if (!p.repoSlug.trim()) {
    errs.repoSlug = "required";
  } else if (!SLUG_PATTERN.test(p.repoSlug)) {
    errs.repoSlug = "expected owner/name";
  }
  if (!p.baseBranch.trim()) {
    errs.baseBranch = "required";
  }
  return errs;
}

export function Project({
  value,
  onChange,
  onCommit,
}: {
  value: ProjectConfig;
  onChange: (next: ProjectConfig) => void;
  onCommit: () => void;
}) {
  const errors = validateProject(value);

  function set<K extends keyof ProjectConfig>(key: K, v: ProjectConfig[K]) {
    onChange({ ...value, [key]: v });
  }

  function setLabel<K extends keyof ProjectConfig["labels"]>(
    key: K,
    v: ProjectConfig["labels"][K],
  ) {
    onChange({ ...value, labels: { ...value.labels, [key]: v } });
  }

  function commitIfValid() {
    if (Object.keys(errors).length === 0) {
      onCommit();
    }
  }

  return (
    <>
      <Panel title="Project">
        <Field
          label="Repo slug"
          description="owner/name on GitHub. Used for `gh pr create` + issue publishing."
          htmlFor="project-repo-slug"
          error={errors.repoSlug}
        >
          <TextInput
            id="project-repo-slug"
            value={value.repoSlug}
            onChange={(v) => set("repoSlug", v)}
            onBlur={commitIfValid}
            width="wide"
            invalid={Boolean(errors.repoSlug)}
          />
        </Field>
        <Field
          label="Base branch"
          description="Default target for PRs. Detected from git remote."
          htmlFor="project-base-branch"
          error={errors.baseBranch}
        >
          <TextInput
            id="project-base-branch"
            value={value.baseBranch}
            onChange={(v) => set("baseBranch", v)}
            onBlur={commitIfValid}
            width="short"
            invalid={Boolean(errors.baseBranch)}
          />
        </Field>
        <Field
          label="Project root"
          description="Where naml runs. Reads `.naml/config.toml`."
          htmlFor="project-root"
        >
          <TextInput
            id="project-root"
            value={value.projectRoot}
            onChange={(v) => set("projectRoot", v)}
            onBlur={commitIfValid}
            width="wide"
          />
        </Field>
        <Field
          label="ADR folder"
          description="Where new ADRs land + where grilling reads them from."
          htmlFor="project-adr"
        >
          <TextInput
            id="project-adr"
            value={value.adrFolder}
            onChange={(v) => set("adrFolder", v)}
            onBlur={commitIfValid}
          />
        </Field>
        <Field
          label="Lifetime tracking since"
          description="First slice JSONL event. Set automatically."
        >
          <span className="mono">{value.lifetimeSince}</span>
        </Field>
      </Panel>

      <Panel title="Labels · GitHub vocabulary">
        <Field
          label="Sprint label prefix"
          description="Applied to every issue in a sprint."
          htmlFor="project-sprint-prefix"
        >
          <TextInput
            id="project-sprint-prefix"
            value={value.labels.sprintPrefix}
            onChange={(v) => setLabel("sprintPrefix", v)}
            onBlur={commitIfValid}
          />
        </Field>
        <Field
          label="Slice label prefix"
          description="Applied to each slice's issue."
          htmlFor="project-slice-prefix"
        >
          <TextInput
            id="project-slice-prefix"
            value={value.labels.slicePrefix}
            onChange={(v) => setLabel("slicePrefix", v)}
            onBlur={commitIfValid}
          />
        </Field>
        <Field
          label="Lifecycle labels"
          description="naml-managed labels per state."
        >
          <TagList
            ariaLabel="lifecycle labels"
            values={value.labels.lifecycle}
            onRemove={(idx) => {
              const next = value.labels.lifecycle.slice();
              next.splice(idx, 1);
              setLabel("lifecycle", next);
              onCommit();
            }}
            onAdd={(label) => {
              setLabel("lifecycle", [...value.labels.lifecycle, label]);
              onCommit();
            }}
          />
        </Field>
      </Panel>
    </>
  );
}
