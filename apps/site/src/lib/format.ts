const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const DATE_SHORT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

export function formatDate(iso: string): string {
  return DATE.format(new Date(`${iso}T00:00:00Z`));
}

export function formatDateShort(iso: string): string {
  return DATE_SHORT.format(new Date(`${iso}T00:00:00Z`));
}

export function formatTimestamp(iso: string): string {
  return DATE_TIME.format(new Date(iso));
}

/** Render a date range, collapsing to a single date when both ends match. */
export function formatDateRange(start: string, end?: string | null): string {
  if (!end || end === start) return formatDate(start);
  return `${formatDate(start)} – ${formatDate(end)}`;
}

export function formatDistance(km: number): string {
  return `${km.toLocaleString("en-GB", { maximumFractionDigits: 1 })} km`;
}

/**
 * Label for a course variant: variant names usually repeat the distance
 * ("14 km", "Marathon (60 km)"), so collapse "name: distance" to just the
 * name when the distance already appears in it. Comparison ignores
 * thousands separators so "1000 km" matches "1,000 km".
 */
export function formatCourseLabel(name: string, km: number): string {
  const distance = formatDistance(km);
  const bare = km.toLocaleString("en-GB", { maximumFractionDigits: 1 });
  const digitsOf = (value: string): string => value.replace(/[^\d.]/g, "");
  // Compare numerically so "8.0" matches "8" and "1,000" matches "1000".
  const numericOf = (value: string): number => Number.parseFloat(value.replace(/[^\d.]/g, "")) || NaN;
  if (
    name === distance ||
    name === bare ||
    digitsOf(name) === digitsOf(bare) ||
    numericOf(name) === km
  ) {
    return distance;
  }
  const collapsed = name
    .replace(new RegExp(`\\s*\\(?${escapeRegExp(bare)}\\s*km\\)?\\s*$`), "")
    .trim();
  return collapsed === name || !collapsed ? `${name}: ${distance}` : collapsed;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function formatElevation(metres: number): string {
  return `${metres.toLocaleString("en-GB")} m`;
}

export function formatKind(format: string): string {
  switch (format) {
    case "stage_race":
      return "Stage race";
    case "point_to_point":
      return "Point to point";
    case "multi_day":
      return "Multi-day";
    default:
      return "Single day";
  }
}

export function formatReview(status: string): string {
  switch (status) {
    case "approved":
      return "Reviewed";
    case "rejected":
      return "Rejected";
    case "false_positive":
      return "False positive";
    default:
      return "Unreviewed";
  }
}
const REGIONAL_INDICATORS = 0x1f1e6;

/** Flag emoji for an ISO 3166-1 alpha-2 country code, or the bare code. */
export function countryFlag(code: string): string {
  if (!/^[A-Z]{2}$/.test(code)) return code;
  return String.fromCodePoint(
    ...[...code].map((char) => REGIONAL_INDICATORS + (char.charCodeAt(0) - 65)),
  );
}

const MONTH_DAY = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

/** "12 Jun" style compact date for the card rail. */
export function formatDateCompact(iso: string): string {
  return MONTH_DAY.format(new Date(`${iso}T00:00:00Z`));
}

/** Year for the card rail when it differs from the current year. */
export function yearOf(iso: string): string {
  return iso.slice(0, 4);
}
