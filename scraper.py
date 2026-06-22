"""receptes-scraper — runner del productor per a la cua privada de Reddit.

Recorre tots els col·lectors de `collectors/COLLECTORS`, fa dedup contra
`output/history.json` (per `dedup_id`) i encua a la cua del Worker els items nous
amb `enqueue(payload)`. El destí (subreddit), el `source` i el `source_label`
viatgen dins de cada payload: els posa el col·lector, no aquest runner.

Afegir un tipus de contingut nou = un fitxer nou a `collectors/` (i a COLLECTORS);
aquí no s'hi toca res.

Ús:
    export WORKER_URL=...  WORKER_WRITE_TOKEN=...
    python scraper.py              # tots els col·lectors
    python scraper.py receptes     # només els col·lectors indicats (per nom)
    python scraper.py --dry-run    # només imprimeix què encolaria (sense xarxa al Worker)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from collectors import COLLECTORS
from queue_client import enqueue

HISTORY = Path(__file__).parent / "output" / "history.json"


def _seen() -> set[str]:
    if HISTORY.exists():
        try:
            return set(json.loads(HISTORY.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _remember(ids: set[str]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(
        json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    # Arguments posicionals = noms de col·lectors a executar (buit → tots). Permet
    # horaris separats al cron: p. ex. `scraper.py preus` i `scraper.py receptes`.
    nomes = {a for a in argv if not a.startswith("-")}
    coneguts = {getattr(c, "__name__", str(c)).split(".")[-1] for c in COLLECTORS}
    for desconegut in sorted(nomes - coneguts):
        print(f"⚠ Col·lector desconegut: {desconegut}")

    seen = _seen()
    nous = 0

    for collector in COLLECTORS:
        nom = getattr(collector, "__name__", str(collector)).split(".")[-1]
        if nomes and nom not in nomes:
            continue
        try:
            items = collector.collect()
        except Exception as e:  # un col·lector que peta no atura la resta
            print(f"⚠ Col·lector «{nom}» ha fallat: {e}")
            continue

        for item in items:
            dedup_id = item["dedup_id"]
            payload = item["payload"]
            if dedup_id in seen:
                print(f"Ja vist ({dedup_id}): {payload['title']}")
                continue

            if dry_run:
                print(f"[dry-run] Encolaria ({dedup_id}): {payload['title']}")
                if payload.get("tipus") == "imatge":
                    print(f"[imatge] {payload.get('url', '')}")
                    if payload.get("comment_markdown"):
                        print(payload["comment_markdown"])
                else:
                    print(payload.get("markdown", ""))
                print("-" * 70)
            else:
                item_id = enqueue(payload)
                print(f"Encuat: {payload['title']} → {item_id}")
                seen.add(dedup_id)
            nous += 1

    if not nous:
        print("Res nou a encolar.")
        return 0

    if not dry_run:
        _remember(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
