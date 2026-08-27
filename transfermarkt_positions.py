#!/usr/bin/env python3
"""
Transfermarkt -> posturile JUCATE efectiv in sezonul 2025/26, toate competitiile
(campionat + cupe + Champions League), pentru cele 36 de echipe din faza de liga
a UEFA Champions League 2025/26.

Inlocuieste perechea de scripturi FBref + Transfermarkt: aici totul vine dintr-o
singura sursa, deci nu mai e nevoie de pasul --merge.

Cum functioneaza (3 pasi, fiecare scrie cache pe disk ca sa poti relua fara sa
re-descarci tot):

  1. --discover     lista celor 36 de cluburi din faza de liga UCL 2025/26,
                     luata direct de pe pagina competitiei de pe Transfermarkt.
  2. --squads        pentru fiecare club, lotul curent (nume jucator, post
                     declarat, link profil Transfermarkt).
  3. --positions     pentru fiecare jucator din loturi, tabelul detaliat de
                     meciuri jucate in sezonul 2025/26 (toate competitiile),
                     de unde extrage postul jucat si minutele per meci, apoi
                     agrega minute per post -> post principal, posturi
                     secundare, detaliu minute/post.

  python transfermarkt_positions.py --discover
  python transfermarkt_positions.py --squads
  python transfermarkt_positions.py --positions

  (sau --all ca sa ruleze cei 3 pasi unul dupa altul)

Instalare:
    python -m venv .venv && source .venv/bin/activate
    pip install requests beautifulsoup4 lxml pandas

Important:
  - Transfermarkt nu are API public si limiteaza/blocheaza scraping-ul agresiv.
    Scriptul foloseste un User-Agent de browser real si o pauza intre cereri
    (implicit 2-4s, vezi --delay). Daca primesti erori HTTP 403/429 repetate,
    mareste --delay sau ruleaza mai rar.
  - Parsarea tabelelor foloseste antetul coloanelor (nu pozitia fixa), pentru
    ca Transfermarkt isi schimba din cand in cand structura HTML. Daca un
    tabel nu se poate parsa, scriptul scrie avertisment + salveaza pagina bruta
    in cache/ ca sa poti inspecta manual ce s-a schimbat.
  - Acest script NU a putut fi testat impotriva Transfermarkt live (mediul in
    care a fost scris nu are acces la internet general), deci trateaza-l ca
    punct de plecare: ruleaza-l local, si daca vreun selector nu se mai
    potriveste, cache/ iti da pagina HTML exacta ca sa ajustezi rapid.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.transfermarkt.com"
CACHE = Path("cache/transfermarkt")
OUT = Path("out")
SEASON_ID = "2025"          # sezonul 2025/26 (Transfermarkt numeroteaza dupa anul de start)
UCL_COMP_PATH = "/uefa-champions-league/startseite/pokalwettbewerb/CL"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TEAMS_JSON = OUT / "ucl_2526_teams.json"
SQUADS_CSV = OUT / "ucl_2526_squads.csv"
POSITIONS_CSV = OUT / "transfermarkt_2526_positions.csv"


def norm(s: str) -> str:
    """Normalizare nume pentru potrivire intre surse."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("-", " ").replace(".", " ").split())


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------


class Fetcher:
    def __init__(self, delay: tuple[float, float] = (2.0, 4.0)) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        CACHE.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, cache_key: str) -> str:
        cache_file = CACHE / f"{cache_key}.html"
        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")

        url = path if path.startswith("http") else BASE + path
        time.sleep(random.uniform(*self.delay))
        resp = self.session.get(url, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} pentru {url}")
        cache_file.write_text(resp.text, encoding="utf-8")
        return resp.text


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Pasul 1 - descoperire echipe (faza de liga UCL 2025/26)
# ---------------------------------------------------------------------------


def discover(fetcher: Fetcher) -> list[dict]:
    """Extrage cele 36 de cluburi din faza de liga UCL de pe pagina competitiei."""
    html = fetcher.get(f"{UCL_COMP_PATH}/saison_id/{SEASON_ID}", "ucl_overview")
    soup = soup_of(html)

    teams: dict[str, dict] = {}
    for a in soup.select('a[href*="/verein/"]'):
        href = a.get("href", "")
        m = re.search(r"/([\w-]+)/[\w-]+/verein/(\d+)", href)
        if not m:
            continue
        slug, club_id = m.group(1), m.group(2)
        name = a.get_text(strip=True) or a.get("title", "").strip()
        if not name or club_id in teams:
            continue
        teams[club_id] = {"club_id": club_id, "slug": slug, "name": name}

    result = list(teams.values())
    OUT.mkdir(exist_ok=True)
    TEAMS_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{len(result)} cluburi gasite pe pagina UCL (asteptat: 36).")
    for t in result:
        print(f"  {t['club_id']:>7}  {t['name']}")
    print(f"\nScris: {TEAMS_JSON}")
    if len(result) != 36:
        print(
            "\nATENTIE: nu am gasit exact 36 de cluburi. Deschide "
            f"cache/transfermarkt/ucl_overview.html si verifica manual "
            "selectorul (structura paginii s-ar putea sa difere)."
        )
    return result


# ---------------------------------------------------------------------------
# Pasul 2 - loturi (kader) per club
# ---------------------------------------------------------------------------


def _parse_squad_table(html: str, team_name: str) -> list[dict]:
    soup = soup_of(html)
    table = soup.select_one("table.items")
    if table is None:
        print(f"  !! nu gasesc tabelul de lot pentru {team_name}", file=sys.stderr)
        return []

    rows = []
    for tr in table.select("tbody > tr"):
        link = tr.select_one('a[href*="/profil/spieler/"]')
        if link is None:
            continue
        m = re.search(r"/([\w-]+)/profil/spieler/(\d+)", link.get("href", ""))
        if not m:
            continue
        slug, player_id = m.group(1), m.group(2)
        name = link.get_text(strip=True)

        # Postul declarat e de obicei intr-un <td> separat (a doua linie a
        # celulei cu numele, sau propria coloana "Position").
        pos_cell = tr.select_one("td.posrela table.inline-table tr:nth-of-type(2) td")
        post_declarat = pos_cell.get_text(strip=True) if pos_cell else ""

        rows.append({
            "echipa": team_name,
            "nume": name,
            "player_id": player_id,
            "slug": slug,
            "post_declarat": post_declarat,
            "profil_url": f"{BASE}/{slug}/profil/spieler/{player_id}",
        })
    return rows


def squads(fetcher: Fetcher) -> pd.DataFrame:
    if not TEAMS_JSON.exists():
        sys.exit("Nu gasesc out/ucl_2526_teams.json. Ruleaza intai --discover.")
    teams = json.loads(TEAMS_JSON.read_text(encoding="utf-8"))

    all_rows = []
    for t in teams:
        path = f"/{t['slug']}/kader/verein/{t['club_id']}/saison_id/{SEASON_ID}/plus/1"
        try:
            html = fetcher.get(path, f"kader_{t['club_id']}")
            rows = _parse_squad_table(html, t["name"])
            all_rows.extend(rows)
            print(f"  OK  {t['name']:<28} {len(rows):>3} jucatori")
        except Exception as exc:
            print(f"  --  {t['name']:<28} {type(exc).__name__}: {exc}", file=sys.stderr)

    df = pd.DataFrame(all_rows)
    if df.empty:
        sys.exit("Nu s-a extras niciun jucator. Verifica cache/transfermarkt/kader_*.html.")
    df["key"] = df["nume"].map(norm)
    OUT.mkdir(exist_ok=True)
    df.to_csv(SQUADS_CSV, index=False, encoding="utf-8-sig")
    print(f"\nScris: {SQUADS_CSV} ({len(df)} jucatori din {len(teams)} echipe)")
    return df


# ---------------------------------------------------------------------------
# Pasul 3 - posturi jucate efectiv, toate competitiile din 2025/26
# ---------------------------------------------------------------------------


def _find_col(headers: list[str], *needles: str) -> int | None:
    for i, h in enumerate(headers):
        h_low = h.lower()
        if any(n in h_low for n in needles):
            return i
    return None


def _parse_player_matches(html: str) -> tuple[dict[str, int], int]:
    """Returneaza (minute pe post, minute_total) din tabelul detaliat de meciuri."""
    soup = soup_of(html)
    table = soup.select_one("table.items")
    if table is None:
        return {}, 0

    header_cells = [th.get_text(strip=True) for th in table.select("thead th")]
    pos_idx = _find_col(header_cells, "pos")
    min_idx = _find_col(header_cells, "min")
    if pos_idx is None or min_idx is None:
        return {}, 0

    pos_minutes: dict[str, int] = {}
    total = 0
    for tr in table.select("tbody > tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(pos_idx, min_idx):
            continue
        pos = cells[pos_idx].get_text(strip=True)
        min_txt = cells[min_idx].get_text(strip=True).replace("'", "").replace(".", "")
        if not pos or not min_txt.isdigit():
            continue
        minutes = int(min_txt)
        pos_minutes[pos] = pos_minutes.get(pos, 0) + minutes
        total += minutes

    return pos_minutes, total


def positions(fetcher: Fetcher) -> pd.DataFrame:
    if not SQUADS_CSV.exists():
        sys.exit("Nu gasesc out/ucl_2526_squads.csv. Ruleaza intai --squads.")
    squads_df = pd.read_csv(SQUADS_CSV)

    results = []
    for _, r in squads_df.iterrows():
        path = (
            f"/{r['slug']}/leistungsdatendetails/spieler/{r['player_id']}"
            f"/saison/{SEASON_ID}/verein/0/liga/0/wettbewerb/0/pos/0/trainer_id/0/plus/1"
        )
        try:
            html = fetcher.get(path, f"matches_{r['player_id']}_{SEASON_ID}")
            pos_minutes, total = _parse_player_matches(html)
        except Exception as exc:
            print(f"  --  {r['nume']:<28} {type(exc).__name__}: {exc}", file=sys.stderr)
            pos_minutes, total = {}, 0

        ordered = sorted(pos_minutes, key=lambda k: -pos_minutes[k])
        results.append({
            "echipa": r["echipa"],
            "nume": r["nume"],
            "post_declarat": r.get("post_declarat", ""),
            "post_principal_jucat": ordered[0] if ordered else "",
            "posturi_jucate": " / ".join(ordered),
            "minute_total_2526": total,
            "detaliu_minute_post": " | ".join(
                f"{k}:{v}" for k, v in sorted(pos_minutes.items(), key=lambda x: -x[1])
            ),
            "profil_url": r["profil_url"],
        })
        print(f"  OK  {r['nume']:<28} {total:>5} min  ->  {ordered[:1]}")

    df = pd.DataFrame(results)
    df["key"] = df["nume"].map(norm)
    OUT.mkdir(exist_ok=True)
    df.to_csv(POSITIONS_CSV, index=False, encoding="utf-8-sig")
    print(f"\nScris: {POSITIONS_CSV} ({len(df)} jucatori)")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="pasul 1: cele 36 de echipe UCL")
    ap.add_argument("--squads", action="store_true", help="pasul 2: loturile echipelor")
    ap.add_argument("--positions", action="store_true", help="pasul 3: posturi jucate 2025/26")
    ap.add_argument("--all", action="store_true", help="ruleaza cei 3 pasi pe rand")
    ap.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"), default=(2.0, 4.0),
                     help="pauza aleatoare intre cereri, in secunde (implicit 2 4)")
    a = ap.parse_args()

    f = Fetcher(delay=tuple(a.delay))

    if a.all:
        discover(f)
        squads(f)
        positions(f)
    elif a.discover:
        discover(f)
    elif a.squads:
        squads(f)
    elif a.positions:
        positions(f)
    else:
        ap.print_help()
