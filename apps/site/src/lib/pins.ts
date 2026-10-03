/** Pinned races, persisted in the visitor's browser (localStorage).

 * Pins are the personal layer on top of the shared calendar: mark the races
 * you consider, then plan around them on the Planner page. Keys are edition
 * ids; everything about the race itself still lives in the catalog, so a pin
 * never duplicates data and unpinning loses nothing.
 */

const KEY = "mtb-pinned-editions";

export function pinnedEditions(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id) => typeof id === "string") : [];
  } catch {
    return [];
  }
}

export function isPinned(editionId: string): boolean {
  return pinnedEditions().includes(editionId);
}

export function togglePin(editionId: string): void {
  const pins = pinnedEditions();
  const next = pins.includes(editionId)
    ? pins.filter((id) => id !== editionId)
    : [...pins, editionId];
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Private browsing or storage disabled: pins work for the session only.
  }
  window.dispatchEvent(new CustomEvent("mtb:pins-changed"));
}

export function pinCount(): number {
  return pinnedEditions().length;
}
