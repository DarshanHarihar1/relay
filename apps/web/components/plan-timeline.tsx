import type { PlanTimelineItem } from "../../../packages/contracts/src";

const statusCopy: Record<PlanTimelineItem["status"], string> = {
  changed: "Changed",
  at_risk: "At risk",
  repaired: "Repaired",
  unresolved: "Unresolved",
  protected: "Protected",
};

export function PlanTimeline({ items }: { items: readonly PlanTimelineItem[] }) {
  const ordered = [...items].sort(
    (left, right) =>
      left.starts_at.localeCompare(right.starts_at) || left.commitment_id.localeCompare(right.commitment_id),
  );
  return (
    <section aria-labelledby="plan-timeline-heading">
      <h2 id="plan-timeline-heading">Repair timeline</h2>
      <ol>
        {ordered.map((item) => (
          <li key={item.commitment_id}>
            <h3>{item.title}</h3>
            <p>{statusCopy[item.status]}</p>
            <p>{item.explanation}</p>
            <time dateTime={item.starts_at}>{new Date(item.starts_at).toLocaleString()}</time>
          </li>
        ))}
      </ol>
    </section>
  );
}
