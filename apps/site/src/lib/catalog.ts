import catalog from "../../../../data/generated/events.json";

export type CourseVariant = (typeof catalog.events)[number]["course_variants"][number];
export type SourceRef = (typeof catalog.events)[number]["source_refs"][number];
export type Edition = (typeof catalog.events)[number];

export const generatedAt: string = catalog.generated_at;
export const schemaVersion: number = catalog.schema_version;

export const editions: Edition[] = [...catalog.events].sort((a, b) =>
  a.date_start === b.date_start
    ? a.name.localeCompare(b.name)
    : a.date_start.localeCompare(b.date_start),
);

const unique = (values: string[]): string[] => [...new Set(values)].sort();

export const countries: string[] = unique(editions.map((e) => e.location.country));

export const kinds: { value: string; label: string }[] = [
  { value: "single_day", label: "Single day" },
  { value: "stage_race", label: "Stage race" },
  { value: "point_to_point", label: "Point to point" },
  { value: "multi_day", label: "Multi-day" },
].filter((kind) => editions.some((e) => e.format === kind.value));

/** Classification values present in the data, as filter options. */
export const classifications: { value: string; label: string }[] = [
  { value: "mtb_marathon", label: "Marathon" },
  { value: "mtb_ultra", label: "Ultra" },
  { value: "mtb_stage_race", label: "Stage race" },
  { value: "mtb_point_to_point", label: "Point to point" },
  { value: "mtb_candidate", label: "Needs review" },
].filter((kind) => editions.some((e) => e.classification === kind.value));

/** ISO date of today in UTC, at the time the site was built. */
export const buildDate: string = new Date().toISOString().slice(0, 10);

export const futureEditions: Edition[] = editions.filter(
  (e) => e.date_end >= buildDate || e.date_start >= buildDate,
);

export function editionById(id: string): Edition | undefined {
  return editions.find((e) => e.id === id);
}

export function editionsForEvent(eventId: string): Edition[] {
  return editions.filter((e) => e.event_id === eventId);
}

/** The most recent edition of a series, for the stable series page. */
export function latestEditionFor(eventId: string): Edition | undefined {
  const all = editionsForEvent(eventId);
  return all.reduce<Edition | undefined>(
    (latest, edition) =>
      !latest || edition.date_start > latest.date_start ? edition : latest,
    undefined,
  );
}

/** All series (distinct event_ids) that have at least one edition. */
export function allEventIds(): string[] {
  return [...new Set(editions.map((e) => e.event_id))];
}

/** True when a newer edition of the same series exists. */
export function hasNewerEdition(edition: Edition): boolean {
  return editionsForEvent(edition.event_id).some(
    (other) => other.date_start > edition.date_start,
  );
}

/** Known adapter namespaces, longest first so prefixes match exactly. */
const SOURCE_PREFIXES = [
  "bawue-radsportverband",
  "radsport-events",
  "epic-series",
];

/** Stable URL slugs: event_id minus its source-adapter prefix.
 *  "radsport-events-riva-del-garda-it" -> "riva-del-garda-it" */
function seriesSlug(eventId: string): string {
  for (const prefix of SOURCE_PREFIXES) {
    if (eventId.startsWith(`${prefix}-`)) {
      return eventId.slice(prefix.length + 1);
    }
  }
  return eventId;
}

const slugToEventId = new Map<string, string>();
const slugCollisions = new Set<string>();
for (const edition of editions) {
  const slug = seriesSlug(edition.event_id);
  const existing = slugToEventId.get(slug);
  if (existing === undefined) {
    slugToEventId.set(slug, edition.event_id);
  } else if (existing !== edition.event_id) {
    slugCollisions.add(slug);
  }
}

/** The event_id for a URL slug, or undefined. */
export function eventIdBySlug(slug: string): string | undefined {
  return slugToEventId.get(slug);
}

/** True when two different series share a slug (they keep full-ID URLs). */
export function slugIsAmbiguous(slug: string): boolean {
  return slugCollisions.has(slug);
}

/** The stable URL slug for a series; ambiguous ones fall back to the full id. */
export function slugForEvent(eventId: string): string {
  const slug = seriesSlug(eventId);
  return slugIsAmbiguous(slug) ? eventId : slug;
}

/** The stable per-edition URL: /events/<slug>/<year>. */
export function editionUrl(edition: Edition): string {
  return `/events/${slugForEvent(edition.event_id)}/${edition.edition}`;
}

/** The stable series URL: /events/<slug>. */
export function seriesUrl(eventId: string): string {
  return `/events/${slugForEvent(eventId)}`;
}

export interface MonthGroup {
  key: string;
  label: string;
  editions: Edition[];
}

const MONTH_LABELS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function monthKey(isoDate: string): string {
  return isoDate.slice(0, 7);
}

export function monthLabel(key: string): string {
  const [year, month] = key.split("-");
  return `${MONTH_LABELS[Number(month) - 1]} ${year}`;
}

/** Editions grouped by the month they start in, with the month they start in. */
export function monthGroups(list: Edition[] = editions): MonthGroup[] {
  const groups = new Map<string, Edition[]>();
  for (const edition of list) {
    const key = monthKey(edition.date_start);
    const bucket = groups.get(key);
    if (bucket) bucket.push(edition);
    else groups.set(key, [edition]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, items]) => ({ key, label: monthLabel(key), editions: items }));
}

export interface CalendarCell {
  iso: string;
  day: number;
  inMonth: boolean;
  editions: Edition[];
}

/**
 * Build a Monday-first grid for one month.
 *
 * Weeks start on Monday because the source calendars are German-speaking and
 * list events from Monday; a Sunday-first grid would strand every race on the
 * wrong row.
 */
export function calendarCells(month: string): CalendarCell[] {
  const [year, monthNumber] = month.split("-").map(Number);
  const first = new Date(Date.UTC(year, monthNumber - 1, 1));
  // getUTCDay() is Sunday-first; shift so Monday is 0.
  const leading = (first.getUTCDay() + 6) % 7;
  const daysInMonth = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();

  const cells: CalendarCell[] = [];
  const cursor = new Date(Date.UTC(year, monthNumber - 1, 1 - leading));

  for (let index = 0; index < 42; index += 1) {
    const iso = cursor.toISOString().slice(0, 10);
    cells.push({
      iso,
      day: cursor.getUTCDate(),
      inMonth: cursor.getUTCMonth() === monthNumber - 1,
      editions: [],
    });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
    if (index >= 34 && cursor.getUTCMonth() !== monthNumber - 1) break;
  }

  // Trim a trailing all-outside-month week so short months do not render an
  // empty row.
  while (cells.length > 7 && cells.slice(-7).every((cell) => !cell.inMonth)) {
    cells.length -= 7;
  }

  const byDate = new Map<string, Edition[]>();
  for (const edition of editions) {
    for (const day of editionDays(edition)) {
      const bucket = byDate.get(day);
      if (bucket) bucket.push(edition);
      else byDate.set(day, [edition]);
    }
  }
  for (const cell of cells) {
    cell.editions = byDate.get(cell.iso) ?? [];
  }
  return cells;
}

/** Every ISO day an edition covers, inclusive of both ends. */
export function editionDays(edition: Edition): string[] {
  const end = (edition as { date_end?: string | null }).date_end ?? edition.date_start;
  const days: string[] = [];
  const cursor = new Date(`${edition.date_start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (cursor <= last) {
    days.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

/** Month keys (YYYY-MM) that have at least one race day, including continuations. */
export function calendarMonths(list: Edition[] = editions): MonthGroup[] {
  const groups = new Map<string, Edition[]>();
  for (const edition of list) {
    for (const day of editionDays(edition)) {
      const key = monthKey(day);
      const bucket = groups.get(key);
      if (bucket) {
        if (!bucket.includes(edition)) bucket.push(edition);
      } else {
        groups.set(key, [edition]);
      }
    }
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, items]) => ({ key, label: monthLabel(key), editions: items }));
}

/** 1-based day number of `iso` within the edition span, 0 when outside. */
export function editionDayNumber(edition: Edition, iso: string): number {
  return editionDays(edition).indexOf(iso) + 1;
}

/** Total days the edition covers, inclusive of both ends. */
export function editionTotalDays(edition: Edition): number {
  return editionDays(edition).length;
}

/** The month an edition starts in, so cards can be filtered by month cheaply. */
export function searchHaystack(edition: Edition): string {
  return [
    edition.name,
    edition.location.name,
    edition.location.region ?? "",
    edition.location.country,
  ]
    .join(" ")
    .toLowerCase();
}