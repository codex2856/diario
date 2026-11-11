#!/usr/bin/env python3
"""El Diario de Wilfredo — descarga feeds RSS/Atom y genera news.json.

Solo usa la librería estándar de Python.
"""

import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

OUTPUT = Path(__file__).with_name("news.json")
MAX_ITEMS = 6
MAX_AGE = timedelta(hours=24)  # las noticias más antiguas se descartan
SUMMARY_LEN = 200
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; ElDiarioDeWilfredo/1.0; +https://github.com)"

# Medios cuyos titulares y resúmenes se traducen al español (medio -> idioma original).
TRANSLATE = {"NOS": "nl", "DutchNews": "en"}
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"

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
        "id": "espana",
        "title": "España",
        "feeds": [
            ("El País", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/espana/portada"),
            ("El Mundo", "https://e00-elmundo.uecdn.es/elmundo/rss/espana.xml"),
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
    # hnrss pone "Article URL: … Comments URL: … Points: …" como descripción
    if summary.startswith("Article URL:") or summary.startswith("Comments URL:"):
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


def date_of(item):
    """Fecha de publicación (RSS pubDate / Atom published|updated / dc:date), o None."""
    for name in ("pubdate", "published", "updated", "date"):
        raw = text_of(child(item, name))
        if not raw:
            continue
        try:
            d = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            try:
                d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    return None


def parse_feed(data, source, now=None):
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(data)
    items = [el for el in root.iter() if local(el.tag) in ("item", "entry")]
    out = []
    for it in items:
        title = clean_html(text_of(child(it, "title")))
        link = link_of(it)
        if not title or not link.startswith("http"):
            continue
        published = date_of(it)
        if published is not None and now - published > MAX_AGE:
            continue  # noticia vieja
        raw = text_of(child(it, "description", "summary", "content", "encoded"))
        summary = shorten(clean_html(raw))
        if summary_is_redundant(title, summary):
            summary = ""
        out.append(
            {"title": title, "summary": summary, "source": source, "link": link, "_date": published}
        )
    # Más recientes primero; las que no traen fecha, al final y en su orden original.
    out.sort(key=lambda x: x["_date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for x in out:
        d = x.pop("_date")
        x["published"] = d.astimezone(timezone.utc).isoformat(timespec="seconds") if d else ""
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


def translate(text, source_lang):
    """Traduce al español con el servicio público de Google Translate.

    Si falla, devuelve None y quien llama se queda con el texto original.
    """
    if not text:
        return text
    query = urllib.parse.urlencode(
        {"client": "gtx", "sl": source_lang, "tl": "es", "dt": "t", "q": text}
    )
    try:
        data = json.loads(fetch(f"{TRANSLATE_URL}?{query}"))
        out = "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
        return out or None
    except Exception as e:  # noqa: BLE001
        warn(f"no se pudo traducir «{text[:40]}…»: {e}")
        return None


def translate_items(items):
    for item in items:
        lang = TRANSLATE.get(item["source"])
        if not lang:
            continue
        title = translate(item["title"], lang)
        if title is None:
            continue  # se deja el original
        summary = translate(item["summary"], lang) if item["summary"] else ""
        item["title"] = title
        item["summary"] = summary if summary is not None else ""
        item["source"] += " · traducido"


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
                    raise ValueError("ninguna noticia de las últimas 24 h")
                per_feed.append(items)
                ok_feeds += 1
                print(f"OK   {source:14} {len(items):3} noticias  {url}")
            except Exception as e:  # noqa: BLE001 — cualquier fallo: avisar y seguir
                failed.append(url)
                warn(f"{source} ({url}) falló: {e}")
        items = interleave(per_feed, MAX_ITEMS, seen)
        translate_items(items)
        sections.append({"id": sec["id"], "title": sec["title"], "items": items})

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
