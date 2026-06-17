"""queue_client.py — Encola un post a la cua privada del Cloudflare Worker.

Genèric i sense dependències (només stdlib). Reutilitzable per qualsevol
productor: només cal definir WORKER_URL i WORKER_WRITE_TOKEN com a variables
d'entorn (secrets de GitHub Actions, o un export local per a proves).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"Falta la variable d'entorn {name}.")
    return v


def enqueue(payload: dict) -> str:
    """POST /enqueue al Worker. Retorna l'id que assigna el Worker.

    Camps del payload: tipus ('text'|'imatge'), title, subreddit, source,
    source_label, i markdown (text) o url (imatge). Opcionals: comment_markdown,
    created_at (ISO-8601).
    """
    url = _env("WORKER_URL").rstrip("/") + "/enqueue"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + _env("WORKER_WRITE_TOKEN"),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("id", "")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"/enqueue HTTP {e.code}: {detail}") from e
