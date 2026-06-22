"""Probe temporal: comprova si una botiga és accessible des de la IP del runner de
GitHub Actions (com vam veure amb Eroski/Carrefour, que fan 403 a datacenter, i
Mercadona, que geo-restringeix a Espanya).

No toca el Worker. Imprimeix l'estat HTTP i si es pot extreure el preu. Esborrar
després d'usar-lo."""
import json
import re
import urllib.request
import urllib.error

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _get(url, accept="text/html"):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "es,ca;q=0.9", "Accept": accept}
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, r.read().decode("utf-8", "replace")


# (etiqueta, url, accept, com trobar el preu)
TESTS = [
    ("Mercadona", "https://tienda.mercadona.es/api/products/10380", "application/json", "json"),
    ("Bonàrea", "https://www.bonarea-online.com/online/producte/llet-sencera/13_9097", "text/html", "html"),
]

for nom, url, accept, mode in TESTS:
    try:
        status, body = _get(url, accept)
        preu = None
        if mode == "json":
            try:
                preu = json.loads(body).get("price_instructions", {}).get("unit_price")
            except Exception:
                preu = None
        else:
            m = re.search(r"content-price[^>]*>\s*<span[^>]*>\s*([\d.,]+)\s*€", body)
            preu = m.group(1) if m else None
        print(f"{nom:10} HTTP {status}  len={len(body):>7}  preu={preu}")
    except urllib.error.HTTPError as e:
        print(f"{nom:10} HTTP {e.code} (HTTPError)  {e.reason}")
    except Exception as e:
        print(f"{nom:10} ERROR {type(e).__name__}: {e}")
