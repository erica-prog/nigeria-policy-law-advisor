/**
 * A citation for an official web publication, in the style Nigerian
 * practitioners use for online sources (OSCOLA-derived): author, 'title',
 * (site, date) <url> accessed date. Deterministic: the model never writes
 * citations itself (AGENTS.md rule 3); it asks for one.
 */
import { SITES, type SiteKey } from "./sites.js";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function formatDate(value: string | Date | undefined): string | undefined {
  if (!value) return undefined;
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return undefined;
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

export function citation(args: {
  site: SiteKey;
  title: string;
  url: string;
  date?: string;
  accessed?: Date;
}): string {
  const site = SITES[args.site];
  const dated = formatDate(args.date);
  const accessed = formatDate(args.accessed ?? new Date());
  const when = dated ? `${site.host}, ${dated}` : site.host;
  return `${site.name}, '${args.title.trim()}' (${when}) <${args.url}> accessed ${accessed}`;
}
