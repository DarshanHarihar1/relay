import { RelayActions } from "../../../components/relay-action-status";

export default async function RepairPlanPage({
  params,
}: {
  params: Promise<{ repairPlanId: string }>;
}) {
  const { repairPlanId } = await params;
  return (
    <main>
      <h1>Repair plan</h1>
      <p>Plan {repairPlanId}</p>
      <RelayActions actions={[]} />
    </main>
  );
}
