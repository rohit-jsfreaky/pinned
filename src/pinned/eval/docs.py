"""The plain-docs baseline's text: each problem's official `docs` URLs, fetched ONCE, cleaned to plain text, cached
under data/docs_cache/. This arm is clearly labelled as the "plain docs" (Context7-like) baseline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import httpx

DEFAULT_DOCS_CACHE = Path("data/docs_cache")
CACHE_STATUSES = {200, 404, 410}   # permanent outcomes; 403/429/5xx and network errors are retried on the next run
_META_REFRESH = re.compile(r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^"']*url=['"]?([^"'>\s]+)""", re.I)
_GH_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
USER_AGENT = "pinned-eval/0.1 (docs baseline; one fetch per URL, cached)"
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "template"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BLOCK = {"p", "div", "section", "article", "main", "li", "ul", "ol", "dt", "dd", "dl", "pre", "table", "tr",
          "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "blockquote"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self._skip_tag = ""     # tag name that opened the skipped region
        self._skip_depth = 0    # nesting of that tag name inside the region
        self._main = 0
        self._pre = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _VOID:
            if tag in ("br", "hr"):
                self._emit("\n")
            return
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        a = dict(attrs)
        if tag in _SKIP or a.get("role") in ("navigation", "search") or "headerlink" in (a.get("class") or ""):
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in ("main", "article") or a.get("role") == "main" or a.get("itemprop") == "articleBody":
            self._main += 1
        if tag == "pre":
            self._pre += 1
        if tag in _BLOCK:
            self._emit("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in ("br", "hr") and not self._skip_depth:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
            return
        if tag in ("main", "article") and self._main:
            self._main -= 1
        if tag == "pre" and self._pre:
            self._pre -= 1
        if tag in _BLOCK:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._emit(data if self._pre else re.sub(r"\s+", " ", data))

    def _emit(self, s: str) -> None:
        self.parts.append(s)
        if self._main:
            self.main_parts.append(s)


def html_to_text(html: str) -> str:
    """Main content (main/article/role=main when present, else the whole body) as plain text."""
    p = _TextExtractor()
    p.feed(html)
    p.close()
    raw = "".join(p.main_parts) if len("".join(p.main_parts).strip()) > 200 else "".join(p.parts)
    lines = [ln.rstrip() for ln in raw.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        if ln.strip() in ("¶", "#", "[source]"):
            continue
        out.append(ln.replace("¶", "").rstrip())
    return "\n".join(out).strip()


@dataclass
class DocPage:
    url: str
    ok: bool
    status: int = 0
    text: str = ""
    error: str = ""


class DocsFetcher:
    """GET each URL once. The raw body is cached as JSON keyed by the URL's hash (only permanent outcomes: 200 and
    404/410); cleaning happens at read time, so improving the cleaner never needs a refetch."""

    def __init__(self, cache_dir: Path = DEFAULT_DOCS_CACHE, timeout: float = 30.0, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.offline = offline
        self._client: httpx.Client | None = None

    def _path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    @staticmethod
    def _page(raw: dict) -> DocPage:
        status = int(raw.get("status", 0))
        if status != 200:
            return DocPage(raw["url"], False, status, error=f"HTTP {status}")
        body = str(raw.get("body", ""))
        is_html = "html" in str(raw.get("content_type", "")) or body.lstrip().startswith("<")
        return DocPage(raw["url"], True, 200, html_to_text(body) if is_html else body.strip())

    def cached(self, url: str) -> DocPage | None:
        p = self._path(url)
        if not p.exists():
            return None
        try:
            return self._page(json.loads(p.read_text()))
        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def fetch_url(url: str) -> str:
        """The URL actually fetched: a GitHub `blob` page is fetched as its raw file (same content, no page chrome)."""
        m = _GH_BLOB.match(url)
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else url

    def _fetch(self, url: str) -> httpx.Response:
        assert self._client is not None
        r = self._client.get(self.fetch_url(url))
        if r.status_code == 200 and len(r.text) < 20000:
            m = _META_REFRESH.search(r.text)   # static "moved" pages (e.g. docs.sympy.org) redirect this way
            if m:
                r = self._client.get(httpx.URL(str(r.url)).join(m.group(1)))
        return r

    def get(self, url: str) -> DocPage:
        hit = self.cached(url)
        if hit is not None:
            return hit
        if self.offline:
            return DocPage(url, False, error="not cached (offline)")
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True,
                                        headers={"User-Agent": USER_AGENT})
        try:
            r = self._fetch(url)
        except httpx.HTTPError as e:
            return DocPage(url, False, error=f"{type(e).__name__}: {e}"[:300])
        raw = {"url": url, "fetched_url": str(r.url), "status": r.status_code,
               "content_type": r.headers.get("content-type", ""), "body": r.text if r.status_code == 200 else ""}
        if r.status_code in CACHE_STATUSES:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._path(url).write_text(json.dumps(raw))
        return self._page(raw)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
