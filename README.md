# receptes-scraper

Productor per a la **cua privada de Reddit** (un Cloudflare Worker + KV).
Genera contingut sobre menjar i l'**encua** perquè l'extensió de Chrome el
publiqui a **r/BonProfit** amb un sol clic. No fa servir l'API de Reddit.

```
col·lectors ──enqueue()──► POST /enqueue (Worker privat) ──► extensió ──► r/BonProfit
```

## Arquitectura

El productor és una col·lecció de **col·lectors** (un per tipus de contingut) i
un **runner** que els recorre, fa dedup i encua:

- `collectors/` — un mòdul per tipus de contingut. Cadascun exposa
  `collect() -> list[dict]` i retorna items amb aquesta forma:

  ```python
  {
    "dedup_id": "preus-2026-W25",      # id estable per no repetir (clau del dedup)
    "payload": {
      "tipus": "text",                 # 'text' | 'imatge'
      "subreddit": "BonProfit",
      "source": "preus",               # tipus de contingut (agrupa al popup)
      "source_label": "Preus súpers",  # com surt etiquetat al popup
      "title": "...",
      "markdown": "...",               # o "url" per a imatges
    },
  }
  ```

  El `subreddit`, el `source` i el `source_label` els posa cada col·lector
  (viatgen dins del payload), no el runner.

- `collectors/preus.py` — primer col·lector: **comparativa setmanal de preus**
  d'una cistella bàsica a Esclat, Caprabo i Ametller Origen. Llegeix la cistella
  de `collectors/cistella.json` (productes, URLs per botiga i unitat de
  normalització), scrapeja preu i format de cada pàgina i normalitza a €/dotzena,
  €/L, €/kg o €/unitat. Resilient: si un preu falla, la cel·la queda `n/d`.

- `scraper.py` — el runner. Recorre `collectors.COLLECTORS`, fa dedup contra
  `output/history.json` (per `dedup_id`) i encua els items nous amb `enqueue()`.

- `queue_client.py` — `enqueue(payload)` genèric: `POST /enqueue` al Worker amb
  un *bearer token*. Sense dependències (stdlib). **Cap secret al codi.**

- `.github/workflows/receptes.yml` — cron setmanal que executa el runner i desa
  l'històric.

**Afegir un tipus de contingut nou** = un fitxer nou a `collectors/` amb
`collect()` + entrada a `COLLECTORS` (a `collectors/__init__.py`). Res més a tocar.

## Configuració (una vegada)

A **Settings → Secrets and variables → Actions**, afegeix:

| Secret | Valor |
|---|---|
| `WORKER_URL` | l'URL del Worker (p.ex. `https://reddit-queue.<usuari>.workers.dev`) |
| `WORKER_WRITE_TOKEN` | el `WRITE_TOKEN` del Worker (el mateix que fan servir els altres productors) |

> El token i l'URL **no es committegen mai** — viuen com a secrets d'Actions.

## Provar-ho

```bash
pip install -r requirements.txt

# contra el teu Worker (o un local amb `wrangler dev`):
export WORKER_URL="https://reddit-queue.<usuari>.workers.dev"
export WORKER_WRITE_TOKEN="el_teu_write_token"
python scraper.py
```

Hauria d'imprimir `Encuat: … → <id>` i l'item ha d'aparèixer al popup de
l'extensió, agrupat com a **Preus súpers**. Una segona execució no encua res
(dedup per `output/history.json`).

Per iterar ràpid sense tocar el Worker:

```bash
python scraper.py --dry-run     # imprimeix què encolaria, sense xarxa al Worker
python -m collectors.preus      # només el col·lector de preus (imprimeix el post)
```
