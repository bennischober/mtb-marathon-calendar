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