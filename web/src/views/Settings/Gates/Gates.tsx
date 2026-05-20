import {
  Button,
  IconButton,
  Muted,
  Panel,
  StatusPill,
  TextInput,
  widgetCss as css,
} from "../components/widgets.tsx";
import type { GateRow } from "../types.ts";

// Cost ordering: cheapest first. Lint < typecheck < test < build is the
// canonical naml ordering — surfaces failures before expensive gates run.
const GATE_COST: Record<string, number> = {
  lint: 0,
  format: 0,
  "web-lint": 0,
  "web-format": 0,
  typecheck: 1,
  "web-typecheck": 1,
  test: 2,
  "web-test": 2,
  build: 3,
  "web-build": 3,
};

const FALLBACK_COST = 99;

export function sortGatesByCost(gates: GateRow[]): GateRow[] {
  return [...gates].sort((a, b) => {
    const ca = GATE_COST[a.name] ?? FALLBACK_COST;
    const cb = GATE_COST[b.name] ?? FALLBACK_COST;
    return ca - cb;
  });
}

function isCheapestFirstOrder(gates: GateRow[]): boolean {
  for (let i = 1; i < gates.length; i++) {
    const prev = gates[i - 1];
    const curr = gates[i];
    if (!prev || !curr) continue;
    const cp = GATE_COST[prev.name] ?? FALLBACK_COST;
    const cc = GATE_COST[curr.name] ?? FALLBACK_COST;
    if (cp > cc) {
      return false;
    }
  }
  return true;
}

export function Gates({
  value,
  onChange,
  onCommit,
}: {
  value: GateRow[];
  onChange: (next: GateRow[]) => void;
  onCommit: () => void;
}) {
  function update(idx: number, patch: Partial<GateRow>) {
    const next = value.map((row, i) => (i === idx ? { ...row, ...patch } : row));
    onChange(next);
  }

  function remove(idx: number) {
    const next = value.slice();
    next.splice(idx, 1);
    onChange(next);
    onCommit();
  }

  function add() {
    onChange([...value, { name: "new-gate", argv: "", status: "idle" }]);
    onCommit();
  }

  function enforceOrdering() {
    if (!isCheapestFirstOrder(value)) {
      onChange(sortGatesByCost(value));
      onCommit();
    }
  }

  return (
    <Panel
      title="Gates · validation commands"
      right={<span className={css.gateOrder}>cheapest first</span>}
    >
      <div style={{ padding: "12px 18px" }}>
        {value.map((row, idx) => (
          <div key={`${row.name}-${idx}`} className={css.gateRow}>
            <TextInput
              ariaLabel={`gate ${idx} name`}
              value={row.name}
              onChange={(v) => update(idx, { name: v })}
              onBlur={() => {
                enforceOrdering();
                onCommit();
              }}
              width="short"
            />
            <TextInput
              ariaLabel={`gate ${row.name} argv`}
              value={row.argv}
              onChange={(v) => update(idx, { argv: v })}
              onBlur={onCommit}
            />
            <StatusPill status={row.status} ranAt={row.ranAt} />
            <IconButton ariaLabel={`remove gate ${row.name}`} onClick={() => remove(idx)}>
              ×
            </IconButton>
          </div>
        ))}
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <Button onClick={add}>+ add gate</Button>
          <Muted>argv is passed verbatim to the subprocess.</Muted>
        </div>
      </div>
    </Panel>
  );
}
