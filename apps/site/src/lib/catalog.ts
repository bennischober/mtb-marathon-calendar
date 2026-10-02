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

export function editionById(id: string): Edition | undefined {
  return editions.find((e) => e.id === id);
}

export function editionsForEvent(eventId: string): Edition[] {
  return editions.filter((e) => e.event_id === eventId);
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