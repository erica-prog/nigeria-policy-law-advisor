/**
 * Reading a WordPress site through its REST API.
 *
 * Two endpoints are enough: `/wp-json/wp/v2/search` for a site-wide search
 * (titles and URLs only) and `/wp-json/wp/v2/{pages,posts}?slug=` to read one
 * item's rendered content, which is HTML we reduce to text. The functions
 * take a `fetch` implementation so tests can answer from recorded fixtures.
 *
 * TypeScript notes: `typeof fetch` is the type of the global function, so a
 * fake with the same signature is accepted. `Promise<T>` plus `async/await`
 * is the same idea as Python's asyncio. Interfaces describe the JSON we
 * expect and nothing more; the server validates the parts it relies on.
 */
import { SITES, type SiteKey } from "./sites.js";

export type FetchLike = typeof fetch;

export const USER_AGENT = "policy-advisor-ng-sources/0.1 (+https://github.com/erica-prog)";
export const REQUEST_TIMEOUT_MS = 20_000;
export const MAX_TEXT_CHARS = 12_000;

export interface SearchHit {
  site: SiteKey;
  id: number;
  kind: string; // "page" (guidance) or "post" (news)
  title: string;
  url: string;
}

export interface PageText {
  site: SiteKey;
  id: number;
  title: string;
  url: string;
  published: string | undefined;
  modified: string | undefined;
  text: string;
  truncated: boolean;
}

interface WpSearchItem {
  id: number;
  title: string;
  url: string;
  type: string;
  subtype: string;
}

interface WpContentItem {
  id: number;
  link: string;
  date?: string;
  modified?: string;
  title: { rendered: string };
  content: { rendered: string };
}

async function getJson(fetchImpl: FetchLike, url: string): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetchImpl(url, {
      headers: { Accept: "application/json", "User-Agent": USER_AGENT },
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Official source returned HTTP ${response.status} for ${url}`);
    }
    return await response.json();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error(`Official source timed out after ${REQUEST_TIMEOUT_MS / 1000} seconds`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export function decodeEntities(value: string): string {
  return value
    .replace(/&#x([0-9a-f]+);/gi, (_, hex: string) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, num: string) => String.fromCodePoint(Number(num)))
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#8217;|&rsquo;/g, "’")
    .replace(/&#8220;|&ldquo;/g, "“")
    .replace(/&#8221;|&rdquo;/g, "”");
}

/** Rendered WordPress HTML to readable text: block elements become line
 * breaks, tags vanish, entities are decoded, whitespace is normalised. */
export function htmlToText(html: string): string {
  const withBreaks = html
    .replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<!--[\s\S]*?-->/gi, " ")
    .replace(/<\/(p|div|li|h[1-6]|tr|br|section|article|blockquote)>|<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, " ");
  return decodeEntities(withBreaks)
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line.length > 0)
    .join("\n");
}

export function searchUrl(site: SiteKey, query: string, limit: number): string {
  const url = new URL(`https://${SITES[site].host}/wp-json/wp/v2/search`);
  url.searchParams.set("search", query);
  url.searchParams.set("per_page", String(limit));
  return url.toString();
}

export async function searchSite(
  fetchImpl: FetchLike,
  site: SiteKey,
  query: string,
  limit: number,
): Promise<SearchHit[]> {
  const data = await getJson(fetchImpl, searchUrl(site, query, limit));
  if (!Array.isArray(data)) throw new Error("search returned a non-list response");
  return (data as WpSearchItem[])
    .filter((item) => typeof item.id === "number" && typeof item.url === "string")
    .map((item) => ({
      site,
      id: item.id,
      kind: item.subtype || item.type,
      title: decodeEntities(String(item.title ?? "")),
      url: item.url,
    }));
}

/** The slug is the last path segment of a WordPress URL. */
export function slugOf(url: URL): string {
  const parts = url.pathname.split("/").filter((p) => p.length > 0);
  return parts[parts.length - 1] ?? "";
}

export async function fetchPage(fetchImpl: FetchLike, site: SiteKey, pageUrl: URL): Promise<PageText> {
  const slug = slugOf(pageUrl);
  if (!/^[a-z0-9-]+$/i.test(slug)) throw new Error("the URL has no readable page slug");
  const fields = "id,link,date,modified,title,content";
  let item: WpContentItem | undefined;
  for (const collection of ["pages", "posts"]) {
    const url = `https://${SITES[site].host}/wp-json/wp/v2/${collection}?slug=${encodeURIComponent(slug)}&_fields=${fields}`;
    const data = await getJson(fetchImpl, url);
    if (Array.isArray(data) && data.length > 0) {
      item = data[0] as WpContentItem;
      break;
    }
  }
  if (!item) throw new Error(`no page or post with slug ${slug} on ${SITES[site].host}`);
  const text = htmlToText(item.content?.rendered ?? "");
  return {
    site,
    id: item.id,
    title: decodeEntities(item.title?.rendered ?? ""),
    url: item.link,
    published: item.date,
    modified: item.modified,
    text: text.slice(0, MAX_TEXT_CHARS),
    truncated: text.length > MAX_TEXT_CHARS,
  };
}
