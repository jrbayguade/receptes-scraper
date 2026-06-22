"""Col·lector «receptes» — recepta destacada del feed de receptes.cat.

Llegeix el feed RSS públic (https://www.receptes.cat/index.xml), tria la recepta
més recent (per `pubDate`) que encara no s'hagi publicat (no és a
`output/history.json`) i en genera un post amb la recepta completa: els
ingredients en llista + l'elaboració sencera + l'enllaç a l'original.

Si la recepta té **foto pròpia** (JSON-LD `image`, descartant la placeholder),
es publica com a **post d'imatge** (la foto) amb la recepta sencera al **primer
comentari** (`comment_markdown`), igual que el pack «explorant» de l'altre
productor: Reddit no renderitza imatges externes al cos d'un selftext, així que
aquesta és l'única manera d'ensenyar la foto. Si no en té (placeholder o cap),
recau en un **post de text** amb la recepta al cos (sempre visible), per no
publicar mai un post d'imatge buit.

Com que el feed ve truncat, la recepta sencera es treu de la pàgina de la recepta
(ingredients del JSON-LD `recipeIngredient`, elaboració del
`<div class="instructions">`). Si la pàgina no es pot raspar, recau en el teaser
del feed perquè el post mai surti buit.

El feed és XML net, així que es parseja amb la stdlib (`xml.etree`); l'HTML de la
pàgina es neteja amb `html.parser` (stdlib). L'HTTP es fa amb `requests`, igual
que el col·lector de preus. És resilient: si el feed falla o no hi ha cap recepta
nova, `collect()` retorna `[]` i el runner continua amb la resta.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from html import unescape
from pathlib import Path

import requests

FEED_URL = "https://www.receptes.cat/index.xml"
HISTORY = Path(__file__).parent.parent / "output" / "history.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "ca,es;q=0.9,en;q=0.8"}
TIMEOUT = 25

SUBREDDIT = "BonProfit"
SOURCE = "receptes"
SOURCE_LABEL = "Receptes"

# Teaser de reserva (només si no es pot raspar la pàgina): es retalla.
RESUM_MAX = 600
SEGUIR = "Seguir llegint"


# --- Neteja d'HTML --------------------------------------------------------- #
class _TextExtractor(HTMLParser):
    """Treu el text d'un fragment HTML conservant els salts de bloc.

    Els elements de bloc (`<p>`, `<h3>`, `<br>`, …) es converteixen en salts de
    línia perquè el text resultant es pugui llegir i normalitzar després.
    """

    _BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCK:
            self._parts.append("\n")
        elif tag == "br":
            # Salt suau: dins d'un paràgraf, manté el text fluït (un espai).
            self._parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _clean_html(html: str) -> str:
    """HTML → text pla, amb els paràgrafs separats per línies en blanc."""
    parser = _TextExtractor()
    parser.feed(html)
    raw = unescape(parser.text())
    # receptes.cat deixa tokens d'enllaç intern com «{3359}» dins del text.
    raw = re.sub(r"\s*\{\d+\}", "", raw)
    # Normalitza espais dins de cada línia i col·lapsa línies buides.
    linies = [re.sub(r"[ \t ]+", " ", l).strip() for l in raw.splitlines()]
    out: list[str] = []
    for l in linies:
        if l:
            out.append(l)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def _resum(description: str) -> str:
    """Teaser curt: talla al «Seguir llegint», neteja l'HTML i acota la llargada."""
    # Talla tot el que vingui a partir de l'enllaç «Seguir llegint ».
    idx = description.find(SEGUIR)
    if idx != -1:
        # Retrocedeix fins a l'inici de l'etiqueta <a ...> que l'embolcalla.
        a = description.rfind("<a", 0, idx)
        description = description[:a] if a != -1 else description[:idx]
    text = _clean_html(description)
    if len(text) <= RESUM_MAX:
        return text
    # Retalla a la darrera frontera de paraula abans del límit i afegeix «…».
    tall = text[:RESUM_MAX].rsplit(None, 1)[0].rstrip(" ,.;:")
    return tall + "…"


# --- Pàgina de la recepta (recepta completa) ------------------------------ #
# El feed ve truncat, així que la recepta sencera es treu de la pàgina: els
# ingredients del JSON-LD `recipeIngredient` (net) i l'elaboració del
# `<div class="instructions">` (el `recipeInstructions` del JSON-LD ve curt).
_INSTRUCTIONS_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*instructions[^"\']*["\'][^>]*>(.*?)</div>',
    re.S | re.I,
)
_LI_RE = re.compile(
    r'<li[^>]*class=["\'][^"\']*ingredient[^"\']*["\'][^>]*>(.*?)</li>',
    re.S | re.I,
)


def _recipe_page(link: str) -> str:
    """HTML de la pàgina de la recepta; cadena buida si no es pot baixar."""
    try:
        return requests.get(link, headers=HEADERS, timeout=TIMEOUT).text
    except requests.RequestException:
        return ""


def _jsonld_recipe(html: str) -> dict:
    """L'objecte JSON-LD de tipus `Recipe` de la pàgina (o {} si no n'hi ha)."""
    for raw in re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict) and "Recipe" in str(obj.get("@type", "")):
                return obj
    return {}


def _split_paragraphs(fragment: str) -> str:
    """Parteix un fragment HTML en paràgrafs (per `<br>`/`<p>`) i el neteja."""
    trossos = re.split(r'<br\s*/?>|</?p[^>]*>', fragment, flags=re.I)
    nets = [_clean_html(t).strip() for t in trossos]
    return "\n\n".join(t for t in nets if t)


def _ingredients(html: str, recipe: dict) -> list[str]:
    """Ingredients: JSON-LD `recipeIngredient`, o els `<li class="ingredient">`."""
    ings = [str(i).strip() for i in (recipe.get("recipeIngredient") or [])]
    if not ings:
        ings = [_clean_html(li).strip() for li in _LI_RE.findall(html)]
    return [i for i in ings if i]


def _elaboracio(html: str, recipe: dict) -> str:
    """Elaboració sencera del `<div class="instructions">` (cada pas un paràgraf).

    Si no es troba, recau en el `recipeInstructions` del JSON-LD (encara que
    pugui venir truncat).
    """
    m = _INSTRUCTIONS_RE.search(html)
    if m:
        passos = _split_paragraphs(m.group(1))
        if passos:
            return passos
    ri = recipe.get("recipeInstructions")
    if isinstance(ri, list):
        trossos = [s if isinstance(s, str) else (s or {}).get("text", "") for s in ri]
        return "\n\n".join(t.strip() for t in trossos if t and t.strip())
    if isinstance(ri, str):
        return ri.strip()
    return ""


# Placeholder de receptes.cat per a receptes sense foto pròpia. Descartar-la
# evita publicar un post d'imatge amb una imatge buida/genèrica.
_PLACEHOLDER_IMG = "thumbphoto/400/default.jpg"


def _image_url(recipe: dict) -> str:
    """URL de la foto pròpia de la recepta, o cadena buida si no en té.

    El camp `image` del JSON-LD pot venir com a cadena, com a llista o com a
    objecte `ImageObject` (amb `url`). Es descarta la placeholder
    (.../thumbphoto/400/default.jpg): aquestes receptes es publiquen com a post
    de text, no com a post d'imatge erroni.
    """
    img = recipe.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("url")
    url = str(img or "").strip()
    if not url or _PLACEHOLDER_IMG in url:
        return ""
    return url


def _cos(ingredients: list[str], elaboracio: str, description: str,
         link: str) -> str:
    """Cos del post: recepta completa (ingredients en llista + elaboració) i
    enllaç a l'original. Si no s'ha pogut raspar la pàgina, recau en el teaser
    del feed perquè el post mai surti buit."""
    parts: list[str] = []
    if ingredients:
        parts.append("**Ingredients**\n\n" + "\n".join(f"- {i}" for i in ingredients))
    if elaboracio:
        parts.append("**Elaboració**\n\n" + elaboracio)
    if not parts:  # la pàgina ha fallat: teaser del feed com a reserva
        parts.append(_resum(description))
    parts.append(f"Recepta completa **via [receptes.cat]({link})**")
    return "\n\n".join(parts)


# --- Utilitats del feed ---------------------------------------------------- #
def _pubdate(item: ET.Element) -> datetime:
    """`pubDate` com a datetime amb timezone; epoch si no es pot interpretar."""
    raw = (item.findtext("pubDate") or "").strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _seen() -> set[str]:
    """Links ja publicats, segons output/history.json (mateix fitxer que el runner)."""
    if HISTORY.exists():
        try:
            return set(json.loads(HISTORY.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def collect() -> list[dict]:
    try:
        resp = requests.get(FEED_URL, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        # Defensa sense dependències contra entitats XML malicioses (billion
        # laughs / XXE): un feed RSS legítim no declara cap DTD ni entitats.
        if b"<!DOCTYPE" in resp.content or b"<!ENTITY" in resp.content:
            raise ValueError("el feed conté una declaració DTD/ENTITY inesperada")
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError, ValueError) as e:
        print(f"  ⚠ receptes: no s'ha pogut llegir el feed: {e}")
        return []

    items = root.findall(".//item")
    if not items:
        return []

    seen = _seen()
    # Candidats no publicats, de més recent a més antic (per pubDate).
    candidats = [
        it for it in sorted(items, key=_pubdate, reverse=True)
        if (it.findtext("link") or "").strip()
        and (it.findtext("link") or "").strip() not in seen
    ]
    if not candidats:
        return []

    # Selecció: per defecte, la més recent. La pàgina de cada candidat es baixa un
    # sol cop i se'n reaprofiten html/recipe més avall.
    item = candidats[0]
    html = _recipe_page((item.findtext("link") or "").strip())
    recipe = _jsonld_recipe(html) if html else {}

    # Override manual (RECEPTES_PREFEREIX_FOTO): per validar el post d'imatge, si
    # la recepta de torn no té foto pròpia, salta a la més recent que en tingui.
    # Si cap candidat en té, es queda amb la més recent (comportament per defecte).
    if (os.environ.get("RECEPTES_PREFEREIX_FOTO", "").strip() not in ("", "0", "false")
            and not _image_url(recipe)):
        for cand in candidats[1:]:
            chtml = _recipe_page((cand.findtext("link") or "").strip())
            crecipe = _jsonld_recipe(chtml) if chtml else {}
            if _image_url(crecipe):
                item, html, recipe = cand, chtml, crecipe
                break

    link = (item.findtext("link") or "").strip()
    titol = (item.findtext("title") or "").strip()
    description = item.findtext("description") or ""

    title = f"Recepta d'avui: {titol}"
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    # Recepta completa: ingredients + elaboració de la pàgina ja baixada.
    # Si la pàgina ha fallat, `_cos` recau en el teaser del feed.
    markdown = _cos(_ingredients(html, recipe), _elaboracio(html, recipe),
                    description, link)
    image = _image_url(recipe)

    payload = {
        "subreddit": SUBREDDIT,
        "source": SOURCE,
        "source_label": SOURCE_LABEL,
        "title": title,
        "created_at": created_at,
    }
    if image:
        # Amb foto pròpia: post d'imatge + recepta sencera al primer comentari.
        payload.update({"tipus": "imatge", "url": image,
                        "comment_markdown": markdown})
    else:
        # Sense foto: post de text, amb la recepta al cos (sempre visible).
        payload.update({"tipus": "text", "markdown": markdown})
    return [{"dedup_id": link, "payload": payload}]


if __name__ == "__main__":
    # Iteració ràpida: imprimeix l'item que encolaria, sense tocar la cua.
    for it in collect():
        print("dedup_id:", it["dedup_id"])
        import pprint
        pprint.pprint(it["payload"])
