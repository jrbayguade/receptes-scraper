"""Col·lector «preus» — comparativa setmanal de preus d'una cistella bàsica.

Llegeix la cistella de `collectors/cistella.json` (productes, URLs per botiga i
unitat de normalització; cap URL hardcodejada aquí) i, per cada producte i
botiga, scrapeja el preu i el format/quantitat reals de la pàgina. Després
normalitza al preu per unitat comparable (€/dotzena, €/L, €/kg, €/unitat) i
genera un sol post en markdown amb la taula comparativa.

Scraping (tot amb `requests`, sense navegador):
  · Esclat (Bonpreu)  → JSON-LD `Product` incrustat (size + offers.price).
  · Caprabo (Eroski)  → bloc `structured_data` incrustat (price) + nom (format).
  · Ametller Origen   → API Salesforce Commerce (SCAPI): token guest SLAS i
                        després shopper-products (price + pricePerUnit).

És resilient: si una botiga o un producte falla, aquella cel·la queda «n/d» i la
resta del post es genera igualment. Respecta el lloc: peticions seqüencials, amb
un User-Agent realista i un petit retard entre crides.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import requests

CISTELLA = Path(__file__).parent / "cistella.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "ca,es;q=0.9,en;q=0.8"}
TIMEOUT = 25
DELAY = 0.8  # segons entre peticions, per no martellejar els servidors

NA = "n/d"


# --- Utilitats de scraping HTTP ------------------------------------------- #
def _get(url: str, headers: dict | None = None, **kw) -> requests.Response:
    r = requests.get(url, headers=headers or HEADERS, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    time.sleep(DELAY)
    return r


# --- Parseig de quantitats i normalització -------------------------------- #
def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _parse_weight_kg(text: str) -> float | None:
    """Pes en kg a partir de text lliure ('400 grams', '0.4kg', 'malla 2 kg')."""
    m = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(kg|kilos?|quilos?|grams?|gr|g)\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    val, unit = _num(m.group(1)), m.group(2).lower()
    return val / 1000 if unit.startswith("g") else val


def _parse_volume_l(text: str) -> float | None:
    """Volum en litres ('1 litre', '1L', '500 ml')."""
    m = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(litres?|litros?|l|ml|cl)\b", text, re.IGNORECASE
    )
    if not m:
        return None
    val, unit = _num(m.group(1)), m.group(2).lower()
    if unit == "ml":
        return val / 1000
    if unit == "cl":
        return val / 100
    return val


def _parse_count(text: str) -> int | None:
    """Nombre d'unitats ('12 per paquet', '2 u', '1 dotzena')."""
    if re.search(r"dotzen", text, re.IGNORECASE):
        return 12
    m = re.search(
        r"(\d+)\s*(?:per\s+paquet|unitats?|uds?\b|u\b|×|x)", text, re.IGNORECASE
    )
    return int(m.group(1)) if m else None


def normalitza(unitat: str, preu: float, format_text: str,
               ppu: float | None = None, ppu_unitat: str | None = None):
    """Retorna (preu_normalitzat, quantitat, mena) per a la unitat objectiu.

    `mena` ∈ {'kg', 'L', 'u'} serveix per mostrar el format brut. Si la botiga ja
    ofereix un preu per unitat (ppu, p.ex. Ametller via API) s'usa de reserva
    quan no es pot deduir el format del text.
    """
    if unitat == "kg":
        w = _parse_weight_kg(format_text)
        if w:
            return preu / w, w, "kg"
        if ppu and ppu_unitat == "kg" and preu:
            return ppu, preu / ppu, "kg"  # pes deduït del preu i el ppu
        return None, None, "kg"
    if unitat == "L":
        v = _parse_volume_l(format_text)
        if v:
            return preu / v, v, "L"
        if ppu and ppu_unitat == "L" and preu:
            return ppu, preu / ppu, "L"
        return None, None, "L"
    if unitat == "dotzena":
        n = _parse_count(format_text)
        if n:
            return preu * 12 / n, n, "u"
        return None, None, "u"
    if unitat == "unitat":
        n = _parse_count(format_text) or 1  # venut a pes/individual → 1 unitat
        return preu / n, n, "u"
    return None, None, ""


# --- Scrapers per botiga --------------------------------------------------- #
def scrape_esclat(url: str) -> dict:
    """Bonpreu/Esclat: el preu i el format són al JSON-LD `Product`."""
    html = _get(url).text
    blocks = re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S
    )
    for raw in blocks:
        data = json.loads(raw)
        if data.get("@type") != "Product":
            continue
        offer = data.get("offers", {}) or {}
        preu = offer.get("price")
        # El format pot venir a `size` o, més fiable, a la descripció.
        text = " ".join(
            str(x) for x in (data.get("size"), data.get("description")) if x
        )
        return {"preu": float(preu) if preu else None, "format_text": text}
    raise ValueError("JSON-LD de producte no trobat")


def scrape_caprabo(url: str) -> dict:
    """Caprabo/Eroski: preu al bloc `structured_data` incrustat; format al nom."""
    html = _get(url).text
    m = re.search(r'"price\\?":\\?"([\d.]+)\\?"', html)
    preu = float(m.group(1)) if m else None
    nom = ""
    t = re.search(r'<meta property="og:title" content="([^"]+)"', html) or \
        re.search(r"<title>([^<]+)</title>", html)
    if t:
        nom = t.group(1)
    return {"preu": preu, "format_text": nom}


# Configuració i token de l'API Salesforce d'Ametller (es resol un cop).
_AMETLLER: dict = {}


def _ametller_config(url: str) -> dict:
    """Extreu clientId/organizationId/shortCode/siteId de la pàgina (no hardcoded)."""
    html = _get(url).text
    m = re.search(
        r'"commerceAPI":\{[^}]*?"parameters":\{([^}]+)\}', html
    )
    if not m:
        raise ValueError("config commerceAPI no trobada")
    params = dict(re.findall(r'"(\w+)":"([^"]+)"', m.group(1)))
    needed = ("clientId", "organizationId", "shortCode", "siteId")
    if not all(k in params for k in needed):
        raise ValueError(f"config incompleta: {params}")
    return params


def _ametller_token(cfg: dict) -> str:
    """Token guest (SLAS, flux públic PKCE) per a la Shopper API."""
    base = f"https://{cfg['shortCode']}.api.commercecloud.salesforce.com"
    org, cid, site = cfg["organizationId"], cfg["clientId"], cfg["siteId"]
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    redirect = f"https://{cfg.get('host', 'www.ametllerorigen.com')}/callback"

    auth = f"{base}/shopper/auth/v1/organizations/{org}/oauth2/authorize"
    r = s.get(
        auth,
        params={
            "client_id": cid,
            "code_challenge": challenge,
            "response_type": "code",
            "redirect_uri": redirect,
            "hint": "guest",
        },
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    loc = r.headers.get("location", "")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    if "code" not in q:
        raise ValueError(f"authorize sense code (HTTP {r.status_code})")

    tok = f"{base}/shopper/auth/v1/organizations/{org}/oauth2/token"
    rt = s.post(
        tok,
        data={
            "grant_type": "authorization_code_pkce",
            "code": q["code"][0],
            "code_verifier": verifier,
            "client_id": cid,
            "redirect_uri": redirect,
            "usid": q.get("usid", [""])[0],
            "channel_id": site,
        },
        timeout=TIMEOUT,
    )
    rt.raise_for_status()
    return rt.json()["access_token"]


def scrape_ametller(url: str) -> dict:
    """Ametller Origen: Salesforce Commerce (SCAPI shopper-products)."""
    if not _AMETLLER:
        cfg = _ametller_config(url)
        _AMETLLER.update(cfg)
        _AMETLLER["token"] = _ametller_token(cfg)

    m = re.search(r"/(\d+)\.html", url)
    if not m:
        raise ValueError(f"id de producte no trobat a {url}")
    pid = m.group(1)
    base = f"https://{_AMETLLER['shortCode']}.api.commercecloud.salesforce.com"
    r = _get(
        f"{base}/product/shopper-products/v1/organizations/"
        f"{_AMETLLER['organizationId']}/products/{pid}",
        params={"siteId": _AMETLLER["siteId"]},
        headers={**HEADERS, "Authorization": "Bearer " + _AMETLLER["token"]},
    )
    d = r.json()
    preu = d.get("price")
    unit = (d.get("unitMeasure") or "").lower()  # 'kg' | 'l' | ''
    ppu_unitat = "kg" if unit == "kg" else "L" if unit == "l" else None
    return {
        "preu": float(preu) if preu is not None else None,
        "format_text": d.get("name") or "",
        "ppu": d.get("pricePerUnit"),
        "ppu_unitat": ppu_unitat,
    }


SCRAPERS = {
    "Esclat": scrape_esclat,
    "Caprabo": scrape_caprabo,
    "Ametller Origen": scrape_ametller,
}


# --- Format del markdown --------------------------------------------------- #
def _fmt(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _label(unitat: str) -> str:
    return {"dotzena": "€/dotzena", "L": "€/L", "kg": "€/kg",
            "unitat": "€/u"}.get(unitat, "€")


def _format_brut(qty: float | None, mena: str) -> str:
    if qty is None:
        return ""
    if mena == "kg":
        return f"{qty * 1000:.0f} g" if qty < 1 else f"{qty:g} kg"
    if mena == "L":
        return f"{qty:g} L"
    if mena == "u":
        return "1 dotzena" if qty == 12 else f"{qty:g} u"
    return ""


def collect() -> list[dict]:
    cfg = json.loads(CISTELLA.read_text(encoding="utf-8"))
    botigues: list[str] = cfg["botigues"]
    productes: list[dict] = cfg["productes"]

    # Matriu de cel·les: cells[(producte_idx, botiga)] = (normalitzat|None, brut_str)
    cells: dict = {}
    totals: dict[str, float] = {b: 0.0 for b in botigues}
    totals_complets: dict[str, bool] = {b: True for b in botigues}
    totals_count: dict[str, int] = {b: 0 for b in botigues}

    for i, prod in enumerate(productes):
        unitat = prod["unitat"]
        for botiga in botigues:
            url = prod["urls"].get(botiga)
            norm, brut = None, ""
            try:
                if not url:
                    raise ValueError("sense URL")
                dades = SCRAPERS[botiga](url)
                norm, qty, mena = normalitza(
                    unitat,
                    dades["preu"],
                    dades.get("format_text", ""),
                    dades.get("ppu"),
                    dades.get("ppu_unitat"),
                )
                if norm is not None and dades["preu"] is not None:
                    brut = f"{_fmt(dades['preu'])} € · {_format_brut(qty, mena)}"
                else:
                    norm = None
            except Exception as e:  # resilient: una cel·la a n/d no fa petar el post
                print(f"  ⚠ {botiga} · {prod['nom']}: {e}")
                norm = None
            cells[(i, botiga)] = (norm, brut)
            if norm is None:
                totals_complets[botiga] = False
            else:
                totals[botiga] += norm
                totals_count[botiga] += 1

    md = _build_markdown(
        cfg, botigues, productes, cells, totals, totals_complets, totals_count
    )
    iso = date.today().isocalendar()
    return [{
        "dedup_id": f"preus-{iso.year}-W{iso.week:02d}",
        "payload": {
            "tipus": "text",
            "subreddit": cfg["subreddit"],
            "source": cfg["source"],
            "source_label": cfg["source_label"],
            "title": (
                f"Comparativa de preus · cistella bàsica · setmana "
                f"{iso.week}/{iso.year}"
            ),
            "markdown": md,
            "created_at": datetime.now(timezone.utc).replace(
                microsecond=0
            ).isoformat(),
        },
    }]


def _build_markdown(cfg, botigues, productes, cells, totals, totals_complets,
                    totals_count) -> str:
    avui = date.today().strftime("%d/%m/%Y")
    out: list[str] = []

    # Capçalera de la taula
    out.append("| Producte | " + " | ".join(botigues) + " |")
    out.append("|---|" + "|".join([":--:"] * len(botigues)) + "|")

    for i, prod in enumerate(productes):
        label = _label(prod["unitat"])
        # Mínim de la fila per marcar el més barat
        vals = [cells[(i, b)][0] for b in botigues]
        mn = min([v for v in vals if v is not None], default=None)
        cel = []
        for b in botigues:
            norm, brut = cells[(i, b)]
            if norm is None:
                cel.append(NA)
                continue
            txt = f"{_fmt(norm)} {label}"
            if brut:
                txt += f" ({brut})"
            if mn is not None and abs(norm - mn) < 1e-9:
                txt = f"**{txt}**"
            cel.append(txt)
        out.append(f"| {prod['nom']} | " + " | ".join(cel) + " |")

    # Fila de total de la cistella. Només es marca el més barat entre les botigues
    # amb la cistella sencera (els totals parcials no són comparables).
    complets = [totals[b] for b in botigues if totals_complets[b]]
    mn_tot = min(complets) if len(complets) >= 2 else None
    cel = []
    for b in botigues:
        if totals_count[b] == 0:
            cel.append(NA)
            continue
        suf = "" if totals_complets[b] else "*"
        txt = f"{_fmt(totals[b])} €{suf}"
        if mn_tot is not None and totals_complets[b] and abs(totals[b] - mn_tot) < 1e-9:
            txt = f"**{txt}**"
        cel.append(txt)
    out.append("| **Total cistella** | " + " | ".join(cel) + " |")

    md = "\n".join(out)

    # Resum
    md += "\n\n" + _resum(botigues, totals, totals_complets)

    # Notes i peu
    if any(not c for c in totals_complets.values()):
        md += (
            "\n\n\\* Total parcial: alguna cel·la marcada com a n/d no s'ha pogut "
            "consultar i no s'inclou a la suma."
        )
    md += (
        "\n\n*El total és la suma dels preus normalitzats de la cistella "
        "(1 dotzena + 1 L per la llet i l'oli + 1 kg dels productes a pes + "
        "1 unitat d'enciam).*"
    )
    md += (
        f"\n\n---\n*Consulta del {avui}. Preus de la botiga online de cada "
        "cadena, normalitzats a la unitat comparable. Poden variar per zona, "
        "estoc i promocions.*"
    )
    return md


def _resum(botigues, totals, totals_complets) -> str:
    ok = {b: totals[b] for b in botigues if totals_complets[b] and totals[b] > 0}
    if not ok:
        return (
            "Aquesta setmana no s'han pogut consultar prou preus per fer la "
            "comparativa. Quin súper us surt més bé a vosaltres?"
        )
    barat = min(ok, key=ok.get)
    car = max(ok, key=ok.get)
    diff = ok[car] - ok[barat]
    frase = (
        f"Aquesta setmana, **{barat}** és qui surt més barat en el conjunt de la "
        f"cistella ({_fmt(ok[barat])} € sumant els preus normalitzats)."
    )
    if car != barat and diff > 0:
        pct = diff / ok[car] * 100
        frase += (
            f" La diferència amb {car}, el més car, és de {_fmt(diff)} € "
            f"(~{pct:.0f}%)."
        )
    frase += (
        " Tingueu en compte que dins de cada producte el rànquing canvia segons "
        "la cadena. Quins productes compreu a cada súper per estalviar?"
    )
    return frase


if __name__ == "__main__":
    # Iteració ràpida: imprimeix el post sense encolar res.
    for item in collect():
        print("dedup_id:", item["dedup_id"])
        print("title:   ", item["payload"]["title"])
        print("-" * 70)
        print(item["payload"]["markdown"])
