export function formatDateTime(ts: string | null | undefined): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

/** "3 minutes ago", "2 days ago"; falls back to the date for anything older than a month. */
export function timeAgo(ts: string | null | undefined, now: number = Date.now()): string {
  if (!ts) return "—";
  const seconds = Math.max(0, Math.round((now - new Date(ts).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const units: [number, string][] = [
    [60 * 60 * 24 * 30, ""],
    [60 * 60 * 24, "day"],
    [60 * 60, "hour"],
    [60, "minute"],
  ];
  for (const [size, unit] of units) {
    if (seconds >= size) {
      if (!unit) return new Date(ts).toLocaleDateString();
      const n = Math.floor(seconds / size);
      return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
    }
  }
  return "just now";
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}
