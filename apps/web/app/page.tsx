"use client";

import { useEffect, useState } from "react";
import { GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut, type User } from "firebase/auth";

import type { DashboardView } from "../../../packages/contracts/src";

import { ActionOutcomes } from "../components/action-outcomes";
import { PlanTimeline } from "../components/plan-timeline";
import { getFirebaseAuth } from "../lib/firebase";
import { useDashboard } from "../lib/dashboard";

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    const auth = getFirebaseAuth();
    return onAuthStateChanged(auth, (nextUser) => {
      setUser(nextUser);
      setAuthReady(true);
    });
  }, []);

  if (!authReady) {
    return <main><p>Loading Relay...</p></main>;
  }
  if (user === null) {
    return (
      <main>
        <h1>Relay</h1>
        <p>Review bounded repair actions and their verified outcomes.</p>
        {authError !== null && <p role="alert">{authError}</p>}
        <button
          type="button"
          onClick={() => {
            setAuthError(null);
            signInWithPopup(getFirebaseAuth(), new GoogleAuthProvider()).catch(() => {
              setAuthError("Sign-in could not be completed.");
            });
          }}
        >
          Sign in with Google
        </button>
      </main>
    );
  }
  return <DashboardShell user={user} />;
}

function DashboardShell({ user }: { user: User }) {
  const dashboard = useDashboard(true);
  const data = dashboard.data;
  return (
    <main>
      <header>
        <h1>Relay</h1>
        <p>Signed in as {user.email ?? "your Google account"}</p>
        <button type="button" onClick={() => void signOut(getFirebaseAuth())}>Sign out</button>
      </header>
      {dashboard.isLoading && data === null && <p>Loading your repair plan...</p>}
      {dashboard.isOffline && (
        <aside role="status">
          <p>Relay is offline. No approval or pickup change was queued.</p>
          <button type="button" onClick={dashboard.refresh}>Try again</button>
        </aside>
      )}
      {data !== null && <DashboardContent data={data} />}
    </main>
  );
}

function DashboardContent({ data }: { data: DashboardView }) {
  return (
    <>
      <PlanTimeline items={data.timeline} />
      <ActionOutcomes outcomes={data.outcomes} />
    </>
  );
}
