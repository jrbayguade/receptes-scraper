# receptes-scraper

Productor d'exemple per a la **cua privada de Reddit** (un Cloudflare Worker + KV).
Fa scraping de receptes i les **encua** perquè l'extensió de Chrome les publiqui a
**r/menjars** amb un sol clic. No fa servir l'API de Reddit.

```
scraper.py ──enqueue()──► POST /enqueue (Worker privat) ──► extensió ──► r/menjars
```

## Com funciona

- `queue_client.py` — funció genèrica `enqueue(payload)` que fa `POST /enqueue` al
  Worker amb un *bearer token*. Sense dependències (stdlib). **Cap secret al codi.**
- `scraper.py` — l'única part que has d'implementar és **`fetch_receptes()`**
  (el teu scraping real). La resta (dedup via `output/history.json` + `enqueue`) ja hi és.
- `.github/workflows/receptes.yml` — cron setmanal que executa l'scraper i desa l'històric.

L'item porta `subreddit: "menjars"`, `source: "receptes"` i `source_label: "Receptes"`,
així es publica al subreddit correcte i surt agrupat al popup com a "Receptes".

## Configuració (una vegada)

A **Settings → Secrets and variables → Actions**, afegeix:

| Secret | Valor |
|---|---|
| `WORKER_URL` | l'URL del Worker (p.ex. `https://reddit-queue.<usuari>.workers.dev`) |
| `WORKER_WRITE_TOKEN` | el `WRITE_TOKEN` del Worker (el mateix que fan servir els altres productors) |

> El token i l'URL **no es committegen mai** — viuen com a secrets d'Actions. Per això
> aquest repo pot ser públic sense exposar res.

## Provar-ho

```bash
# contra el teu Worker (o un local amb `wrangler dev`):
export WORKER_URL="https://reddit-queue.<usuari>.workers.dev"
export WORKER_WRITE_TOKEN="el_teu_write_token"
python scraper.py
```

Hauria d'imprimir `Encuat: … → <id>` i l'item ha d'aparèixer al popup de l'extensió.
Una segona execució no encua res (dedup per `output/history.json`).

## El que has de fer

1. Implementa **`fetch_receptes()`** a `scraper.py` (retorna `[{"id","title","markdown"}]`).
2. Afegeix els dos secrets (taula de dalt).
3. (Opcional) Ajusta el `cron` del workflow.

Res a tocar a l'extensió ni al Worker: el pack nou s'hi endolla sol.
