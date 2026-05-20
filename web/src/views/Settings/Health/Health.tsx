import { Health as HealthView } from "../../Health/Health.tsx";

// Re-exposed inside the Settings tab structure so the left-nav can render
// the full Health canvas without crowding the configuration forms.
export function Health() {
  return <HealthView />;
}
