"use client";

import { useState } from "react";

import { openUberHandoff } from "../lib/relay-api";

type Props = {
  actionId: string;
};

export function UberHandoffButton({ actionId }: Props) {
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      const result = await openUberHandoff(actionId);
      if (result.state === "handoff_opened") {
        window.location.assign(result.url);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" onClick={handleClick} disabled={busy}>
      {busy ? "Opening Uber…" : "Open Uber"}
    </button>
  );
}
