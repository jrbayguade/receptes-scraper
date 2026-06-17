"""receptes-scraper — productor d'exemple per a la cua de Reddit (Cloudflare Worker).

Flux: fetch_receptes() → filtra les ja vistes (output/history.json) → enqueue() →
recorda-les. L'única part que has de canviar és fetch_receptes(): posa-hi el teu
scraping real (RSS/HTML). La resta (dedup + enqueue) ja és reutilitzable tal qual.

Publica a r/menjars i surt agrupat al popup de l'extensió com a "Receptes".
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from queue_client import enqueue

# --- Configuració del pack -------------------------------------------------- #
SUBREDDIT = "BonProfit"      # el destí viatja amb cada item
SOURCE = "receptes"          # id de la font (nou)
SOURCE_LABEL = "Receptes"    # com surt agrupat al popup

HISTORY = Path(__file__).parent / "output" / "history.json"


def fetch_receptes() -> list[dict]:
    """TODO: SUBSTITUEIX-HO pel teu scraping real.

    Ha de retornar una llista de receptes amb aquesta forma:
        {"id": "<id estable, p.ex. la URL>", "title": "...", "markdown": "## ..."}
    L'"id" ha de ser estable entre execucions perquè el dedup funcioni.

    Exemple amb requests + BeautifulSoup (descomenta i adapta):
        import requests
        from bs4 import BeautifulSoup
        html = requests.get("https://example.cat/receptes", timeout=20).text
        soup = BeautifulSoup(html, "html.parser")
        ...
    """
    return [
        {
            "id": "exemple-fricando",
            "title": "Recepta de la setmana: Fricandó de vedella amb moixernons",
            "markdown": (
                "## Fricandó de vedella amb moixernons\n\n"
                "**Ingredients:** vedella, moixernons, ceba, tomàquet, vi blanc…\n\n"
                "**Passos:** enfarina i marca la vedella, sofregeix la ceba…\n\n"
                "*(Exemple generat per l'esquelet — substitueix `fetch_receptes()`.)*"
            ),
        },
    ]


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


def main() -> int:
    seen = _seen()
    nous = [r for r in fetch_receptes() if r.get("id") and r["id"] not in seen]
    if not nous:
        print("Res nou a encolar.")
        return 0

    for r in nous:
        item_id = enqueue({
            "tipus": "text",
            "title": r["title"],
            "subreddit": SUBREDDIT,
            "source": SOURCE,
            "source_label": SOURCE_LABEL,
            "markdown": r["markdown"],
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        })
        print(f"Encuat: {r['title']} → {item_id}")
        seen.add(r["id"])

    _remember(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
