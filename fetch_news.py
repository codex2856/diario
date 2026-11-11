#!/usr/bin/env python3
"""El Diario de Wilfredo — descarga feeds RSS/Atom y genera news.json.

Solo usa la librería estándar de Python.
"""

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(__file__).with_name("news.json")
MAX_ITEMS = 6
SUMMARY_LEN = 200
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; ElDiarioDeWilfredo/1.0; +https://github.com)"

SECTIONS = [
    {
        "id": "mundo",
        "title": "Mundo",
        "feeds": [
            ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml"),
        ],
    },
    {
        "id": "politica",
        "title": "Política",
        "feeds": [
            ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/temas/politica/rss.xml"),
            ("Infobae", "https://www.infobae.com/arc/outboundfeeds/rss/category/politica/"),
        ],
    },
    {
        "id": "deportes",
        "title": "Deportes",
        "feeds": [
            ("ESPN Deportes", "https://espndeportes.espn.com/espn/rss/news"),
            ("Marca", "https://e00-marca.uecdn.es/rss/portada.xml"),
        ],
    },
    {
        "id": "farandula",
        "title": "Farándula",
        "feeds": [
            ("Infobae", "https://www.infobae.com/arc/outboundfeeds/rss/category/teleshow/"),
        ],
    },
    {
        "id": "paises-bajos",
        "title": "Países Bajos",
        "feeds": [
            ("NOS", "https://feeds.nos.nl/nosnieuwsalgemeen"),
            ("DutchNews", "https://www.dutchnews.nl/feed/"),
        ],
    },
    {
        "id": "latam",
        "title": "Latinoamérica y Venezuela",
        "feeds": [
            ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/temas/america_latina/rss.xml"),
            ("Infobae", "https://www.infobae.com/arc/outboundfeeds/rss/category/america/venezuela/"),
        ],
    },
    {
        "id": "tecnologia",
        "title": "Tecnología y programación",
        "feeds": [
            ("Hacker News", "https://hnrss.org/frontpage?points=150"),
            ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ],
    },
]


def warn(msg):
    print(f"AVISO: {msg}", file=sys.stderr)


def local(tag):
    """Nombre de etiqueta sin namespace: '{http://...}title' -> 'title'."""
    return tag.rsplit("}", 1)[-1].lower()


def child(el, *names):
    for c in el:
        if local(c.tag) in names:
            return c
    return None


def text_of(el):
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def clean_html(raw):
    """Quita etiquetas HTML, decodifica entidades y normaliza espacios."""
    if not raw:
        return ""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def shorten(s, n=SUMMARY_LEN):
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:.-—")
    return cut + "…"


def normalize(s):
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def summary_is_redundant(title, summary):
    t, s = normalize(title), normalize(summary)
    if not s:
        return True
    return s == t or t.startswith(s) or (s.startswith(t) and len(s) - len(t) < 15)


def link_of(item):
    # Atom: <link rel="alternate" href="..."/>; RSS: <link>texto</link>
    candidates = [c for c in item if local(c.tag) == "link"]
    for c in candidates:
        href = c.get("href")
        if href and c.get("rel", "alternate") == "alternate":
            return href.strip()
    for c in candidates:
        if c.get("href"):
            return c.get("href").strip()
        if text_of(c):
            return text_of(c)
    guid = child(item, "guid", "id")
    if guid is not None and text_of(guid).startswith("http"):
        return text_of(guid)
    return ""


def parse_feed(data, source):
    root = ET.fromstring(data)
    items = [el for el in root.iter() if local(el.tag) in ("item", "entry")]
    out = []
    for it in items:
        title = clean_html(text_of(child(it, "title")))
        link = link_of(it)
        if not title or not link.startswith("http"):
            continue
        raw = text_of(child(it, "description", "summary", "content", "encoded"))
        summary = shorten(clean_html(raw))
        if summary_is_redundant(title, summary):
            summary = ""
        out.append({"title": title, "summary": summary, "source": source, "link": link})
    return out


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def interleave(lists, limit, seen):
    """Toma una noticia de cada medio por turnos, sin enlaces repetidos.

    `seen` se comparte entre secciones para que una noticia no salga dos veces.
    """
    result = []
    idx = [0] * len(lists)
    while len(result) < limit:
        progressed = False
        for i, lst in enumerate(lists):
            while idx[i] < len(lst):
                item = lst[idx[i]]
                idx[i] += 1
                key = item["link"].split("#")[0].rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)
                progressed = True
                break
            if len(result) >= limit:
                break
        if not progressed:
            break
    return result


def main():
    sections, ok_feeds, failed, seen = [], 0, [], set()
    for sec in SECTIONS:
        per_feed = []
        for source, url in sec["feeds"]:
            try:
                items = parse_feed(fetch(url), source)
                if not items:
                    raise ValueError("el feed no contiene noticias")
                per_feed.append(items)
                ok_feeds += 1
                print(f"OK   {source:14} {len(items):3} noticias  {url}")
            except Exception as e:  # noqa: BLE001 — cualquier fallo: avisar y seguir
                failed.append(url)
                warn(f"{source} ({url}) falló: {e}")
        sections.append(
            {"id": sec["id"], "title": sec["title"], "items": interleave(per_feed, MAX_ITEMS, seen)}
        )

    if ok_feeds == 0:
        print("ERROR: ningún feed respondió; news.json no se modifica.", file=sys.stderr)
        sys.exit(1)

    data = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sections": sections,
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(len(s["items"]) for s in sections)
    print(f"news.json escrito: {total} noticias, {ok_feeds} feeds OK, {len(failed)} fallidos.")


if __name__ == "__main__":
    main()
