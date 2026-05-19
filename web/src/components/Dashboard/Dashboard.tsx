import dashboardJson from "../../fixtures/dashboard.json";
import styles from "./Dashboard.module.css";
import type {
  CostSlice,
  DashboardData,
  HealthStat,
  InboxBlock,
  SliceStateColor,
  SprintRowData,
} from "./types";

const data = dashboardJson as DashboardData;

/** Hash-routing navigation — compatible with the slice-2 router (`#/sprint/:id`). */
function openSprint(id: string): void {
  if (typeof window !== "undefined") {
    window.location.hash = `#/sprint/${id}`;
  }
}

export function Dashboard(): JSX.Element {
  return (
    <section className={styles.dashboard} aria-label="Dashboard">
      <Hero data={data} />
      <div className={styles.dashGrid}>
        <Timeline data={data} />
        <InboxSidecar inbox={data.inbox} />
      </div>
    </section>
  );
}

function Hero({ data }: { data: DashboardData }): JSX.Element {
  const { project, cost, health, healthTertiary } = data;
  return (
    <header className={styles.hero}>
      <div className={styles.heroTop}>
        <div>
          <span className={styles.projectName}>{project.name}</span>
          <span className={styles.projectState} aria-label={`Project state: ${project.state}`}>
            <span className={`${styles.projectStateDot} naml-pulse`} />
            {project.state}
          </span>
          <div className={styles.projectSub}>
            {project.lanes} lanes · {project.branch} · {project.sprintsShipped} sprints shipped ·
            last merge {project.lastMergeMinutesAgo}m ago
          </div>
        </div>
        <div className={styles.burnLine}>
          ↘ daily burn <strong>${project.dailyBurn7dAvgDollars.toFixed(2)} avg · 7d</strong>
        </div>
      </div>

      <div className={styles.primaryCost} aria-label="Cost timeline">
        <CostStat label="TODAY" slice={cost.today} formatDollars />
        <CostStat label="THIS WEEK" slice={cost.thisWeek} formatDollars />
        <CostStat label="LAST 30 DAYS" slice={cost.last30d} formatDollars />
        <CostStat
          label="LIFETIME · PROJECT"
          value={`$${cost.lifetime.dollars.toFixed(2)}`}
          subParts={[`since ${cost.lifetime.since}`]}
          lifetime
        />
      </div>

      <div className={styles.healthRow} aria-label="Pipeline health">
        {health.map((h) => (
          <HealthStatCell key={h.label} stat={h} />
        ))}
      </div>

      <div
        className={`${styles.healthRow} ${styles.healthRowTertiary}`}
        aria-label="Pipeline health — extended"
      >
        {healthTertiary.map((h) => (
          <HealthStatCell key={h.label} stat={h} />
        ))}
      </div>
    </header>
  );
}

interface CostStatProps {
  label: string;
  slice?: CostSlice;
  value?: string;
  subParts?: string[];
  formatDollars?: boolean;
  lifetime?: boolean;
}

function CostStat({
  label,
  slice,
  value,
  subParts,
  formatDollars,
  lifetime,
}: CostStatProps): JSX.Element {
  const displayValue = value ?? (slice ? formatCost(slice.dollars, Boolean(formatDollars)) : "—");
  const subs: string[] = subParts
    ? [...subParts]
    : slice
      ? [...(slice.tokens ? [`${slice.tokens} tokens`] : []), ...(slice.delta ? [slice.delta] : [])]
      : [];
  return (
    <div className={styles.pStat}>
      <div className={styles.pStatLabel}>{label}</div>
      <div className={`${styles.pStatValue}${lifetime ? ` ${styles.pStatValueLifetime}` : ""}`}>
        {displayValue}
      </div>
      {subs.length > 0 && (
        <div className={styles.pStatSub}>
          {subs.map((s, i) => {
            const isDelta = slice?.delta === s;
            const cls = isDelta
              ? slice?.deltaDirection === "up"
                ? `${styles.pStatDelta} ${styles.pStatDeltaUp}`
                : styles.pStatDelta
              : undefined;
            return (
              // biome-ignore lint/suspicious/noArrayIndexKey: fixture order is stable
              <span key={i} className={cls}>
                {s}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatCost(dollars: number, asDollars: boolean): string {
  return asDollars ? `$${dollars.toFixed(2)}` : dollars.toFixed(2);
}

function HealthStatCell({ stat }: { stat: HealthStat }): JSX.Element {
  const valueCls = [
    styles.healthValue,
    stat.tone === "ok" ? styles.healthValueOk : "",
    stat.tone === "warn" ? styles.healthValueWarn : "",
    stat.tone === "danger" ? styles.healthValueDanger : "",
  ]
    .filter(Boolean)
    .join(" ");
  const trendCls = [
    styles.healthTrend,
    stat.trendTone === "up" ? styles.healthTrendUp : "",
    stat.trendTone === "dn" ? styles.healthTrendDn : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={`${styles.healthStat}${stat.alert ? ` ${styles.healthStatAlert}` : ""}`}>
      <span className={styles.healthLabel}>{stat.label}</span>
      <span className={valueCls}>{stat.value}</span>
      {stat.trend && <span className={trendCls}>{stat.trend}</span>}
    </div>
  );
}

function Timeline({ data }: { data: DashboardData }): JSX.Element {
  const { active, queued, recent } = data.sprints;
  return (
    <div className={styles.timeline}>
      <SprintGroup label="ACTIVE" count={String(active.length)} sprints={active} isActive />
      <SprintGroup label="QUEUED" count={String(queued.length)} sprints={queued} />
      <SprintGroup label="RECENT" count={`last ${recent.length}`} sprints={recent} />
    </div>
  );
}

interface SprintGroupProps {
  label: string;
  count: string;
  sprints: SprintRowData[];
  isActive?: boolean;
}

function SprintGroup({ label, count, sprints, isActive }: SprintGroupProps): JSX.Element {
  return (
    <>
      <h2 className={styles.sectionTitle}>
        {label} <span className={styles.sectionCount}>{count}</span>
      </h2>
      {sprints.map((s) => (
        <SprintRow key={s.id} sprint={s} highlight={Boolean(isActive)} />
      ))}
    </>
  );
}

function SprintRow({
  sprint,
  highlight,
}: {
  sprint: SprintRowData;
  highlight: boolean;
}): JSX.Element {
  const pillCls = `${styles.sprintPill} ${pillClassFor(sprint.state)}`;
  return (
    <button
      type="button"
      onClick={() => openSprint(sprint.id)}
      className={`${styles.sprintRow}${highlight ? ` ${styles.sprintRowActive}` : ""}`}
      data-sprint-id={sprint.id}
      aria-label={`Open sprint ${sprint.title}`}
    >
      <div className={styles.sprintMini}>
        {sprint.slices.map((cell, i) => (
          <span
            // biome-ignore lint/suspicious/noArrayIndexKey: slice order is positional
            key={i}
            className={`${styles.sprintMiniCell} ${miniCellClass(cell)}`}
            data-slice-state={cell}
          />
        ))}
      </div>
      <div className={styles.sprintTitle}>
        <div className={styles.sprintTitleText}>
          {sprint.title}
          {sprint.titleSuffix && (
            <span className={styles.sprintTitleSuffix}>{sprint.titleSuffix}</span>
          )}
        </div>
        <div className={styles.sprintSubtitle}>{sprint.subtitle}</div>
      </div>
      <span className={pillCls}>{sprint.stateLabel}</span>
      <div className={styles.sprintMeta}>
        {sprint.metaTopRight && <strong>{sprint.metaTopRight}</strong>}
        {sprint.metaTopRight && sprint.metaBottomRight && <br />}
        {sprint.metaBottomRight && <strong>{sprint.metaBottomRight}</strong>}
      </div>
      <span className={styles.sprintOpen}>{sprint.openLabel}</span>
    </button>
  );
}

function pillClassFor(state: SprintRowData["state"]): string {
  switch (state) {
    case "executing":
      return styles.pillExecuting ?? "";
    case "planned":
      return styles.pillPlanned ?? "";
    case "complete":
      return styles.pillComplete ?? "";
    case "signoff":
      return styles.pillSignoff ?? "";
    case "failed":
      return styles.pillFailed ?? "";
    default:
      return "";
  }
}

function miniCellClass(state: SliceStateColor): string {
  return styles[state] ?? "";
}

function InboxSidecar({ inbox }: { inbox: InboxBlock }): JSX.Element {
  const isWarn = inbox.unfiled > 3;
  const countCls = `${styles.inboxCount}${isWarn ? ` ${styles.inboxCountWarn}` : ""}`;
  return (
    <aside className={styles.inbox} aria-label="Feedback inbox">
      <div className={styles.inboxHeader}>
        <span>FEEDBACK INBOX</span>
        <span className={countCls} data-testid="inbox-count" data-warn={isWarn ? "true" : "false"}>
          {inbox.unfiled} unfiled
        </span>
      </div>
      {inbox.bullets.map((b, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: fixture order is stable
        <div key={i} className={styles.inboxBullet}>
          {b.text}
          <span className={styles.inboxBulletSource}>{b.source}</span>
        </div>
      ))}
      <button type="button" className={styles.inboxLink}>
        {inbox.link}
      </button>
    </aside>
  );
}
