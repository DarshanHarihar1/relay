import { RelayActions } from "../../../components/relay-action-status";

export default function RepairPlanPage({
  params,
}: {
  params: { repairPlanId: string };
}) {
  return (
    <main>
      <h1>Repair plan</h1>
      <p>Plan {params.repairPlanId}</p>
      <RelayActions actions={[]} />
    </main>
  );
}
