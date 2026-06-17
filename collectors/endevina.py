"""Col·lector «endevina» — joc «Endevina el plat» generat amb IA (DeepSeek).

Demana a DeepSeek (API OpenAI-compatible) que triï un plat típic català i en
redacti una pista enigmàtica (descripció abstracta o llista d'ingredients
d'incògnit), i genera un sol post de text per a la comunitat: la pista visible i
la solució amagada amb el spoiler de Reddit (`>!plat!<`).

Pensat per a un cron dos cops per setmana (dimecres i diumenge). `dedup_id` és la
data, així que el runner encua com a molt un joc per dia.

La key és obligatòria: si falta `DEEPSEEK_API_KEY` o DeepSeek la rebutja
(HTTP 401/403), `collect()` llança `SystemExit` perquè el workflow surti vermell
i sàpigues que cal arreglar-la (no es publica res en silenci). Els errors
transitoris de l'API es propaguen com a excepció normal (el runner els registra
com a avís i continua amb la resta de col·lectors).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

import requests

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TIMEOUT = 60

SUBREDDIT = "BonProfit"
SOURCE = "endevina"
SOURCE_LABEL = "Endevina-plat"

# Senyera amb emojis: quatre quadres roigs envoltats de grocs.
SENYERA = "🟨🟥🟨🟥🟨🟥🟨🟥🟨"

# To de marca per al contingut generat (veure memòria «to-contingut-catala»).
SYSTEM_PROMPT = (
    "Ets el game master de r/BonProfit, una comunitat catalana de cuina a Reddit. "
    "Crees un joc d'endevinar plats. Escrius sempre en català natural, directe i "
    "col·loquial, adequat per a usuaris de Reddit. Evites estructures artificials, "
    "paraules excessivament formals i introduccions de farciment tipus «és un "
    "plaer ajudar-te». Vas al gra, fas servir formats Markdown calents (negretes, "
    "llistes) i mantens un to un punt sarcàstic i apassionat amb el menjar."
)
USER_PROMPT = (
    "Tria UN plat típic de la cuina catalana (per exemple capipota, trinxat, "
    "fricandó, escudella, esqueixada, suquet, fideuà, calçots amb romesco, "
    "mandonguilles amb sípia, crema catalana...). Escriu una PISTA enigmàtica "
    "perquè la gent l'endevini: una descripció abstracta i codificada, o una "
    "llista d'ingredients «d'incògnit», sense dir mai el nom del plat ni paraules "
    "massa òbvies. Que sigui divertida i jugable als comentaris, amb una mica de "
    "Markdown (negretes, potser una llista). Respon NOMÉS amb un objecte JSON amb "
    'aquestes dues claus exactes: {"plat": "<nom del plat>", "pista": "<la pista '
    'en markdown>"}.'
)


def _api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("Falta la variable d'entorn DEEPSEEK_API_KEY.")
    return key


def _demana_joc() -> tuple[str, str]:
    """Crida DeepSeek i retorna (plat, pista). La key és obligatòria."""
    key = _api_key()
    resp = requests.post(
        DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 1.2,
            "max_tokens": 700,
        },
        timeout=TIMEOUT,
    )
    # Problema de key → peta fort (workflow vermell) perquè se sàpiga.
    if resp.status_code in (401, 403):
        raise SystemExit(
            f"DeepSeek rebutja la DEEPSEEK_API_KEY (HTTP {resp.status_code}): "
            "revisa el secret."
        )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    dades = json.loads(content)
    plat = (dades.get("plat") or "").strip()
    pista = (dades.get("pista") or "").strip()
    if not plat or not pista:
        raise ValueError(f"resposta de DeepSeek incompleta: {dades!r}")
    return plat, pista


def collect() -> list[dict]:
    plat, pista = _demana_joc()
    avui = date.today()
    title = f"{SENYERA} [JOC] Endevina el plat català · {avui.strftime('%d/%m/%Y')}"
    markdown = (
        f"{pista}\n\n"
        "---\n"
        "👇 Ho saps? Etziba la teva resposta als comentaris.\n\n"
        f"Solució (no facis trampes): >!{plat}!<"
    )
    payload = {
        "tipus": "text",
        "subreddit": SUBREDDIT,
        "source": SOURCE,
        "source_label": SOURCE_LABEL,
        "title": title,
        "markdown": markdown,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return [{"dedup_id": f"endevina-{avui.isoformat()}", "payload": payload}]


if __name__ == "__main__":
    # Iteració ràpida: imprimeix el joc que encolaria (cal DEEPSEEK_API_KEY).
    for it in collect():
        print("dedup_id:", it["dedup_id"])
        print("title:   ", it["payload"]["title"])
        print("-" * 70)
        print(it["payload"]["markdown"])
