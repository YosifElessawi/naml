import type {
  ChangeEvent,
  KeyboardEvent,
  ReactNode,
} from "react";
import { useEffect, useState } from "react";
import css from "./widgets.module.css";

export function Panel({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={css.panel}>
      <header className={css.sectionHeader}>
        <span>{title}</span>
        {right ? <span>{right}</span> : null}
      </header>
      <div>{children}</div>
    </section>
  );
}

export function Field({
  label,
  description,
  htmlFor,
  error,
  children,
}: {
  label: string;
  description?: string;
  htmlFor?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={css.field}>
      <div>
        {htmlFor ? (
          <label className={css.label} htmlFor={htmlFor}>
            {label}
          </label>
        ) : (
          <span className={css.label}>{label}</span>
        )}
        {description ? <div className={css.desc}>{description}</div> : null}
      </div>
      <div className={css.control}>
        {children}
        {error ? (
          <span role="alert" className={css.errorText}>
            {error}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function classes(...cls: Array<string | undefined | false>): string {
  return cls.filter(Boolean).join(" ");
}

interface TextInputProps {
  id?: string;
  value: string;
  onChange: (next: string) => void;
  onBlur?: () => void;
  width?: "default" | "wide" | "short";
  invalid?: boolean;
  placeholder?: string;
  ariaLabel?: string;
}

export function TextInput({
  id,
  value,
  onChange,
  onBlur,
  width = "default",
  invalid = false,
  placeholder,
  ariaLabel,
}: TextInputProps) {
  return (
    <input
      id={id}
      aria-label={ariaLabel}
      className={classes(
        css.input,
        width === "wide" && css.wide,
        width === "short" && css.short,
        invalid && css.invalid,
      )}
      value={value}
      placeholder={placeholder}
      onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
      onBlur={onBlur}
    />
  );
}

interface SelectInputProps {
  id?: string;
  value: string;
  options: readonly string[];
  onChange: (next: string) => void;
  onBlur?: () => void;
  width?: "default" | "wide" | "short";
  ariaLabel?: string;
}

export function SelectInput({
  id,
  value,
  options,
  onChange,
  onBlur,
  width = "default",
  ariaLabel,
}: SelectInputProps) {
  return (
    <select
      id={id}
      aria-label={ariaLabel}
      className={classes(
        css.select,
        width === "wide" && css.wide,
        width === "short" && css.short,
      )}
      value={value}
      onChange={(e: ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}
      onBlur={onBlur}
    >
      {options.map((opt) => (
        <option key={opt} value={opt}>
          {opt}
        </option>
      ))}
    </select>
  );
}

interface ToggleProps {
  id?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  ariaLabel: string;
}

export function Toggle({ id, checked, onChange, ariaLabel }: ToggleProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      className={classes(css.toggle, checked && css.on)}
      onClick={() => onChange(!checked)}
    />
  );
}

interface SliderProps {
  id?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (next: number) => void;
  onBlur?: () => void;
  ariaLabel: string;
}

export function Slider({
  id,
  value,
  min,
  max,
  step = 1,
  onChange,
  onBlur,
  ariaLabel,
}: SliderProps) {
  return (
    <input
      id={id}
      type="range"
      aria-label={ariaLabel}
      className={css.slider}
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e: ChangeEvent<HTMLInputElement>) =>
        onChange(Number(e.target.value))
      }
      onBlur={onBlur}
    />
  );
}

export function SliderValue({ value }: { value: number }) {
  return <span className={css.sliderValue}>{value}</span>;
}

export function Muted({ children }: { children: ReactNode }) {
  return <span className={css.muted}>{children}</span>;
}

export function Unit({ children }: { children: ReactNode }) {
  return <span className={css.unit}>{children}</span>;
}

const STATUS_CLASS: Record<string, string> = {
  ok: css.statusOk ?? "",
  running: css.statusRunning ?? "",
  fail: css.statusFail ?? "",
  idle: css.statusIdle ?? "",
};

export function StatusPill({
  status,
  ranAt,
}: {
  status: "ok" | "running" | "fail" | "idle";
  ranAt?: string;
}) {
  if (status === "ok") {
    return (
      <span className={STATUS_CLASS.ok}>
        ✓{ranAt ? ` last ran ${ranAt}` : ""}
      </span>
    );
  }
  if (status === "running") {
    return <span className={STATUS_CLASS.running}>▶ running</span>;
  }
  if (status === "fail") {
    return <span className={STATUS_CLASS.fail}>✗</span>;
  }
  return <span className={STATUS_CLASS.idle}>—</span>;
}

export function Button({
  variant = "default",
  onClick,
  disabled,
  children,
  type = "button",
}: {
  variant?: "default" | "primary" | "danger";
  onClick?: () => void;
  disabled?: boolean;
  children: ReactNode;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      className={classes(
        css.btn,
        variant === "primary" && css.primary,
        variant === "danger" && css.danger,
      )}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

export function IconButton({
  onClick,
  ariaLabel,
  children,
  disabled,
}: {
  onClick: () => void;
  ariaLabel: string;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      className={css.iconBtn}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

export function TagList({
  values,
  onRemove,
  onAdd,
  ariaLabel,
}: {
  values: string[];
  onRemove: (idx: number) => void;
  onAdd?: (next: string) => void;
  ariaLabel?: string;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");

  function commit() {
    const trimmed = draft.trim();
    if (trimmed && onAdd) {
      onAdd(trimmed);
    }
    setDraft("");
    setAdding(false);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      commit();
    } else if (e.key === "Escape") {
      setDraft("");
      setAdding(false);
    }
  }

  return (
    <div className={css.tagList} aria-label={ariaLabel}>
      {values.map((v, i) => (
        <span key={`${v}-${i}`} className={css.tag}>
          {v}
          <button
            type="button"
            aria-label={`remove ${v}`}
            onClick={() => onRemove(i)}
          >
            ×
          </button>
        </span>
      ))}
      {onAdd ? (
        adding ? (
          <input
            className={css.input}
            autoFocus
            value={draft}
            placeholder="label…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            onBlur={commit}
            aria-label="new label"
          />
        ) : (
          <button
            type="button"
            className={css.tagAdd}
            onClick={() => setAdding(true)}
          >
            + add
          </button>
        )
      ) : null}
    </div>
  );
}

export function SavedBadge({ savedAt }: { savedAt: number | null }) {
  const [, force] = useState(0);
  useEffect(() => {
    if (savedAt == null) {
      return;
    }
    const id = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [savedAt]);
  if (savedAt == null) {
    return <span className={classes(css.savedBadge, css.idle)}>—</span>;
  }
  const delta = Math.max(0, Math.round((Date.now() - savedAt) / 1000));
  const label = delta === 0 ? "just now" : `${delta}s ago`;
  return (
    <span className={css.savedBadge} aria-live="polite">
      Saved {label}
    </span>
  );
}

export const widgetCss = css;
