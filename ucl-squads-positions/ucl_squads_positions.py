#!/usr/bin/env python3
"""
UCL 2026/27 - loturi + posturi (principal / secundare) din Transfermarkt.

Sursa: instanta locala a proiectului open-source `felipeall/transfermarkt-api`
(FastAPI care face scraping structurat pe transfermarkt.com).

Pornire API local (o singura data):
    git clone https://github.com/felipeall/transfermarkt-api.git
    cd transfermarkt-api && docker build -t transfermarkt-api . \
        && docker run -d -p 8000:8000 transfermarkt-api

Rulare:
    pip install requests
    python ucl_squads_positions.py --season 2026
    python ucl_squads_positions.py --season 2025   # loturile de sezonul trecut

Iesiri:
    out/ucl_<season>_squads.csv
    out/ucl_<season>_squads.md
    cache/  (raspunsuri brute, ca sa nu re-interoghezi la fiecare rulare)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configurare
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("TFMKT_API", "http://localhost:8000")
COMPETITION_ID = "CL"          # UEFA Champions League pe Transfermarkt
SLEEP = float(os.getenv("TFMKT_SLEEP", "0.8"))   # pauza intre requesturi (secunde)
TIMEOUT = 30
RETRIES = 3

CACHE = Path("cache")
OUT = Path("out")

# Cele 36 de echipe din faza-ligă 2026/27 (urnele UEFA, 26.08.2026).
# `query` = textul trimis catre /clubs/search/{query} daca rezolvarea
# automata prin competitie nu functioneaza.
TEAMS_2026_27 = [
    # Urna 1
    ("Paris Saint-Germain", "Paris Saint-Germain"),
    ("Bayern München", "Bayern Munich"),
    ("Real Madrid", "Real Madrid"),
    ("Liverpool", "Liverpool FC"),
    ("Inter", "Inter Milan"),
    ("Manchester City", "Manchester City"),
    ("Arsenal", "Arsenal FC"),
    ("Barcelona", "FC Barcelona"),
    ("Atlético Madrid", "Atletico Madrid"),
    # Urna 2
    ("Borussia Dortmund", "Borussia Dortmund"),
    ("Roma", "AS Roma"),
    ("Sporting CP", "Sporting CP"),
    ("Aston Villa", "Aston Villa"),
    ("FC Porto", "FC Porto"),
    ("Manchester United", "Manchester United"),
    ("Club Brugge", "Club Brugge KV"),
    ("Real Betis", "Real Betis Balompie"),
    ("PSV Eindhoven", "PSV Eindhoven"),
    # Urna 3
    ("Feyenoord", "Feyenoord Rotterdam"),
    ("Lille", "LOSC Lille"),
    ("Bodø/Glimt", "FK Bodo/Glimt"),
    ("Napoli", "SSC Napoli"),
    ("RB Leipzig", "RB Leipzig"),
    ("Villarreal", "Villarreal CF"),
    ("Fenerbahçe", "Fenerbahce"),
    ("Shakhtar Donetsk", "Shakhtar Donetsk"),
    ("Galatasaray", "Galatasaray"),
    # Urna 4
    ("Slavia Praha", "Slavia Prague"),
    ("Slovan Bratislava", "Slovan Bratislava"),
    ("VfB Stuttgart", "VfB Stuttgart"),
    ("AEK Athens", "AEK Athens"),
    ("LASK", "LASK Linz"),
    ("Como", "Como 1907"),
    ("Lens", "RC Lens"),
    ("Viking", "Viking FK"),
    ("Sabah", "Sabah FK"),
]

# ---------------------------------------------------------------------------
# Utilitare HTTP + cache
# ---------------------------------------------------------------------------


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "_" for c in text).strip("_").lower()


def get(path: str, params: dict | None = None, cache_key: str | None = None) -> dict | None:
    """GET cu cache pe disc si retry."""
    if cache_key:
        CACHE.mkdir(exist_ok=True)
        cached = CACHE / f"{cache_key}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))

    url = f"{BASE_URL}{path}"
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(5 * attempt)
                continue
            r.raise_for_status()
            data = r.json()
            if cache_key:
                (CACHE / f"{cache_key}.json").write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
            time.sleep(SLEEP)
            return data
        except requests.RequestException as exc:
            print(f"    ! {path} (incercarea {attempt}/{RETRIES}): {exc}", file=sys.stderr)
            time.sleep(2 * attempt)
    return None


# ---------------------------------------------------------------------------
# Pasul 1 - identificarea cluburilor
# ---------------------------------------------------------------------------


def resolve_clubs(season: str) -> list[tuple[str, str]]:
    """Returneaza [(club_id, nume)]. Incearca intai endpointul de competitie."""
    data = get(f"/competitions/{COMPETITION_ID}/clubs",
               {"season_id": season},
               cache_key=f"competition_{COMPETITION_ID}_{season}")

    clubs = (data or {}).get("clubs") or []
    if len(clubs) >= 30:
        print(f"  {len(clubs)} cluburi preluate direct din competitia {COMPETITION_ID}/{season}")
        return [(c["id"], c["name"]) for c in clubs]

    print("  Endpointul de competitie nu a returnat lotul complet "
          "(pagina Transfermarkt se populeaza dupa tragere). Trec pe cautare dupa nume.")

    resolved: list[tuple[str, str]] = []
    for display_name, query in TEAMS_2026_27:
        res = get(f"/clubs/search/{requests.utils.quote(query)}",
                  cache_key=f"clubsearch_{slug(query)}")
        results = (res or {}).get("results") or []
        if not results:
            print(f"    ? NEGASIT: {display_name} -> completeaza manual ID-ul")
            continue
        best = results[0]
        resolved.append((best["id"], display_name))
        print(f"    {display_name:<24} -> id={best['id']} ({best.get('name')})")
    return resolved


# ---------------------------------------------------------------------------
# Pasul 2 + 3 - lot si posturi
# ---------------------------------------------------------------------------


def club_squad(club_id: str, season: str) -> list[dict]:
    data = get(f"/clubs/{club_id}/players",
               {"season_id": season},
               cache_key=f"squad_{club_id}_{season}")
    return (data or {}).get("players") or []


def player_profile(player_id: str) -> tuple[str, list[str], str]:
    """Postul principal, posturile secundare si numarul de tricou."""
    data = get(f"/players/{player_id}/profile", cache_key=f"player_{player_id}")
    pos = (data or {}).get("position") or {}
    return pos.get("main") or "", pos.get("other") or [], (data or {}).get("shirtNumber") or ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026",
                    help="2026 = sezonul 2026/27, 2025 = 2025/26")
    ap.add_argument("--no-profiles", action="store_true",
                    help="doar postul principal din lot, fara profilul fiecarui jucator (mult mai rapid)")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)

    print(f"API: {BASE_URL}")
    try:
        requests.get(f"{BASE_URL}/docs", timeout=10)
    except requests.RequestException:
        sys.exit(f"Nu pot contacta {BASE_URL}. Porneste containerul transfermarkt-api mai intai.")

    print("\n[1/3] Identific cluburile...")
    clubs = resolve_clubs(args.season)
    if not clubs:
        sys.exit("Niciun club identificat.")

    rows = []
    print(f"\n[2/3] Preiau loturile ({len(clubs)} echipe)...")
    for i, (club_id, club_name) in enumerate(clubs, 1):
        squad = club_squad(club_id, args.season)
        print(f"  {i:>2}. {club_name:<24} {len(squad):>3} jucatori")
        for p in squad:
            main_pos = p.get("position") or ""
            other: list[str] = []
            shirt = ""
            if not args.no_profiles and p.get("id"):
                pm, other, shirt = player_profile(p["id"])
                main_pos = pm or main_pos
            all_pos = [main_pos] + [o for o in other if o and o != main_pos]
            rows.append({
                "echipa": club_name,
                "club_id": club_id,
                "player_id": p.get("id", ""),
                "nume": p.get("name", ""),
                "numar": shirt,
                "post_principal": main_pos,
                "posturi_secundare": " / ".join(other),
                "posturi": " / ".join([x for x in all_pos if x]),
                "varsta": p.get("age", ""),
                "picior": p.get("foot", ""),
                "valoare_eur": p.get("marketValue", ""),
            })

    print(f"\n[3/3] Scriu rezultatele ({len(rows)} jucatori)...")
    csv_path = OUT / f"ucl_{args.season}_squads.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    md_path = OUT / f"ucl_{args.season}_squads.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write(f"# UEFA Champions League {args.season}/{int(args.season)+1} — loturi și posturi\n")
        current = None
        for r in rows:
            if r["echipa"] != current:
                current = r["echipa"]
                f.write(f"\n## {current}\n\n")
            f.write(f"- **{r['nume']}** — {r['posturi']}\n")

    print(f"  {csv_path}\n  {md_path}")


if __name__ == "__main__":
    main()
