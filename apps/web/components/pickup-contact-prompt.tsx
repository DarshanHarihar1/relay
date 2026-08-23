"use client";

import { useState } from "react";

import type { PickerSessionView } from "../../../packages/contracts/src";

import { searchPickerContacts, submitPickup } from "../lib/relay-api";

type Props = {
  commitmentId: string;
  version: number;
  onRefresh?: () => void;
};

export function PickupContactPrompt({ commitmentId, version, onRefresh }: Props) {
  const [mode, setMode] = useState<"start" | "picker" | "manual">("start");
  const [query, setQuery] = useState("");
  const [picker, setPicker] = useState<PickerSessionView | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function chooseNoPickup() {
    await send({ selection: "no_pickup", expected_version: version });
  }

  async function search() {
    setPending(true);
    setError(null);
    try {
      setPicker(await searchPickerContacts(query));
    } catch {
      setError("Contacts could not be loaded. You can enter a number manually.");
    } finally {
      setPending(false);
    }
  }

  async function choosePicker(index: number) {
    if (picker === null) return;
    await send({
      selection: "google_picker",
      picker_session_id: picker.session_id,
      picker_contact_index: index,
      expected_version: version,
    });
  }

  async function saveManual() {
    await send({
      selection: "manual",
      manual_display_name: displayName,
      manual_phone_number: phoneNumber,
      expected_version: version,
    });
  }

  async function send(command: Parameters<typeof submitPickup>[1]) {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await submitPickup(commitmentId, command);
      onRefresh?.();
      setMode("start");
      setPicker(null);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Pickup could not be updated.";
      setError(message.includes("409") ? "This pickup changed. Review the current plan." : message);
      onRefresh?.();
    } finally {
      setPending(false);
      setPhoneNumber("");
    }
  }

  return (
    <section aria-labelledby="pickup-heading">
      <h2 id="pickup-heading">Are you being picked up?</h2>
      {error !== null && <p role="alert">{error}</p>}
      {mode === "start" && (
        <>
          <button type="button" onClick={() => void chooseNoPickup()} disabled={pending}>No</button>
          <button type="button" onClick={() => setMode("picker")} disabled={pending}>Choose contact</button>
          <button type="button" onClick={() => setMode("manual")} disabled={pending}>Enter number</button>
        </>
      )}
      {mode === "picker" && (
        <div>
          <label>
            Search contacts
            <input value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <button type="button" onClick={() => void search()} disabled={pending || query.trim().length < 2}>Search</button>
          {picker !== null && (
            <ul>
              {picker.contacts.map((contact, index) => (
                <li key={contact.display_name}>
                  <button type="button" onClick={() => void choosePicker(index)} disabled={pending}>
                    {contact.display_name} ({contact.phones.map((phone) => `ending ${phone.last4}`).join(", ")})
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {mode === "manual" && (
        <div>
          <label>
            Name
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </label>
          <label>
            Phone number
            <input type="tel" value={phoneNumber} onChange={(event) => setPhoneNumber(event.target.value)} />
          </label>
          <button type="button" onClick={() => void saveManual()} disabled={pending}>Save pickup contact</button>
        </div>
      )}
    </section>
  );
}
