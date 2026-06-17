"""Col·lectors de contingut per a la cua de Reddit.

Cada col·lector és un mòdul amb `collect() -> list[dict]`. Cada item té la forma:

    {
      "dedup_id": "<id estable per no repetir>",
      "payload": {
        "tipus": "text" | "imatge",
        "subreddit": "...",
        "source": "...",          # tipus de contingut (agrupa al popup)
        "source_label": "...",    # com surt etiquetat al popup
        "title": "...",
        "markdown": "...",        # o "url" per a imatges
      },
    }

Afegir un tipus de contingut nou = un fitxer nou aquí + entrada a COLLECTORS.
"""
from __future__ import annotations

from . import preus

COLLECTORS = [preus]
