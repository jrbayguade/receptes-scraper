# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Guia dels **fonaments no obvis**. La forma del payload i el "com afegir un
col·lector" ja estan al `README.md`; aquí hi ha el que costa més de deduir
llegint fitxers solts. **Codi, logs, sortida d'usuari i commits: en català.**

## Què és

Un **productor** per a una cua privada de Reddit. Genera contingut sobre menjar i
l'**encua** (`POST /enqueue` a un Cloudflare Worker); després una **extensió de
Chrome** el publica a **r/BonProfit**. Tres col·lectors:

| Col·lector | Contingut | Font |
|---|---|---|
| `preus` | Comparativa setmanal de preus d'una cistella | Scraping de botigues (JSON-LD / dades incrustades / API), cap navegador |
| `receptes` | Recepta completa (ingredients + elaboració) | Feed RSS de receptes.cat + scraping de la pàgina (JSON-LD `recipeIngredient` + `<div class="instructions">`) |
| `endevina` | Joc «Endevina el plat» amb solució amb spoiler | Generat per DeepSeek (API OpenAI-compatible) |

## Com es publica (FONAMENTAL)

**No es publica per l'API de Reddit.** El productor només encua; la publicació la
fa a mà una **extensió de Chrome** (repo separat `~/code/reddit-extension`) amb la
sessió de Reddit de l'usuari. Implicacions que cal recordar:

- **`tipus: "text"`** → post de text; el cos és `markdown`.
- **`tipus: "imatge"`** → post d'enllaç a la imatge (`url`); el text va a
  `comment_markdown`, que l'extensió publica com a **primer comentari**
  (best-effort). **En mode "Prova a r/test" l'extensió OMET el comentari** —
  només es publica en una publicació real. No és un bug.
- **Reddit NO renderitza imatges externes** (`![](url)`) al cos d'un selftext.
  Per ensenyar una foto cal el post d'imatge; per ensenyar text sempre, post de text.
- El popup agrupa per `source_label`.

## Arquitectura

- `collectors/` — un mòdul per tipus de contingut amb `collect() -> list[dict]`
  (items `{dedup_id, payload}`); registrats a `collectors/__init__.py`
  (`COLLECTORS`). El `subreddit`/`source`/`source_label` els posa el col·lector,
  no el runner.
- `scraper.py` — runner: recorre `COLLECTORS`, fa **dedup** pel `dedup_id` contra
  `output/history.json` i encua els nous. **Accepta noms de col·lector com a
  arguments posicionals** per executar-ne un subconjunt (clau per als crons
  separats); sense arguments, els corre tots.
- `queue_client.py` — `enqueue(payload)` (stdlib): `POST /enqueue` amb bearer token.
- `output/history.json` — el dedup. Els workflows el **commiten de tornada al
  repo** després de cada run, així persisteix entre execucions (per això les
  receptes/jocs no es repeteixen).

## Comandes

```bash
pip install -r requirements.txt

# Runner (cal WORKER_URL + WORKER_WRITE_TOKEN; endevina cal també DEEPSEEK_API_KEY)
python scraper.py                  # tots els col·lectors
python scraper.py receptes         # només un (o més) per nom
python scraper.py --dry-run        # imprimeix què encolaria, sense xarxa al Worker

# Previsualitzar un col·lector sol (imprimeix el post, no encua)
python -m collectors.preus
python -m collectors.receptes
DEEPSEEK_API_KEY=... python -m collectors.endevina
```

No hi ha framework de tests; la verificació és executar els col·lectors i el runner.

### Provar el camí d'encolat sense el Worker real

Els secrets viuen NOMÉS a GitHub Actions (no en local). Per verificar el productor
de punta a punta sense el Worker real, aixeca un mock HTTP que implementi
`POST /enqueue` retornant `{"id": ...}` i apunta-hi `WORKER_URL`; confirma el
payload i la capçalera `Authorization`. Sembra `output/history.json` (llista de
`dedup_id`) per forçar quin item tria cada col·lector.

## Crons (per col·lector, DST-aware)

Cada col·lector té el SEU workflow i el seu dia; tots executen
`python scraper.py <nom>`:

| Workflow | Dia(es) | Col·lector |
|---|---|---|
| `preus.yml` | dilluns 07:52 CAT | `preus` |
| `receptes.yml` | dimarts i dijous 07:52 CAT | `receptes` |
| `endevina.yml` | dimecres i diumenge 07:52 CAT | `endevina` |

GitHub Actions corre sempre en **UTC** i no s'ajusta al canvi d'hora. El patró
DST-aware (replicar-lo per a qualsevol cron nou): **dues entrades cron** (una per
CEST `52 5`, una per CET `52 6`) + un job `finestra` que mira
`TZ=Europe/Madrid date +%z` i només deixa passar la que toca (les execucions
manuals `workflow_dispatch` sempre passen).

## Gotchas

- **Cloudflare `403 / error code: 1010`**: el Worker veta el User-Agent per
  defecte d'`urllib`/`requests` (`Python-urllib/...`). `queue_client.py` envia un
  UA de navegador realista; cal mantenir-lo.
- **`endevina`**: si falta `DEEPSEEK_API_KEY` o DeepSeek la rebutja (401/403),
  `collect()` llança `SystemExit` a propòsit → el workflow surt **vermell** (el joc
  no té fallback estàtic, així se sap que cal arreglar la key).
- **`receptes`**: les receptes sense foto pròpia exposen un `image` JSON-LD que és
  un placeholder (`.../thumbphoto/400/default.jpg`); es filtra perquè no es
  publiquin com a post d'imatge erroni.
- **Resiliència** (convenció): una font/crida que falla no ha de tombar el procés;
  el feed XML es parseja amb stdlib amb una guarda anti-XXE (rebuig de DTD/ENTITY).

## Secrets (Settings ▸ Secrets ▸ Actions)

`WORKER_URL`, `WORKER_WRITE_TOKEN` (compartits amb els altres productors del mateix
Worker) i `DEEPSEEK_API_KEY` (només `endevina`).
