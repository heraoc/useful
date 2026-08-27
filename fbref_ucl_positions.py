#!/usr/bin/env python3
"""
FBref -> posturile JUCATE efectiv in sezonul 2025/26 (campionat + Champions League).

Complementar scriptului Transfermarkt: acolo iei eticheta declarata de post,
aici iei ce a jucat omul de fapt, cu minute.

Instalare (o singura data):
    python -m venv .venv && source .venv/bin/activate
    pip install soccerdata pandas
    mkdir -p ~/soccerdata/config && cp league_dict.json ~/soccerdata/config/

    # soccerdata 1.9 foloseste Selenium pentru FBref -> ai nevoie de Chrome instalat.
    # macOS: brew install --cask google-chrome

Utilizare:
    python fbref_ucl_positions.py --discover        # pasul 0: numele exacte de competitii
    python fbref_ucl_positions.py --extract         # pasul 1: descarca datele
    python fbref_ucl_positions.py --merge out/ucl_2026_squads.csv   # pasul 2: imbina cu Transfermarkt
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import pandas as pd

OUT = Path("out")
SEASON = "2526"          # 2025/26
SEASON_SINGLE_YEAR = "2025"   # pentru Eliteserien (calendar de primavara-toamna)

# Ligile din care provin cele 36 de echipe calificate in 2026/27.
LEAGUES = [
    "Big 5 European Leagues Combined",   # ENG, ESP, ITA, GER, FRA -> 22 din 36 de echipe
    "INT-Champions League",
    "POR-Primeira Liga",
    "NED-Eredivisie",
    "BEL-Pro League",
    "TUR-Super Lig",
    "UKR-Premier League",
    "CZE-First League",
    "GRE-Super League",
    "AUT-Bundesliga",
]
LEAGUES_SINGLE_YEAR = ["NOR-Eliteserien"]   # Bodø/Glimt, Viking


def norm(s: str) -> str:
    """Normalizare nume pentru potrivire intre surse."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("-", " ").replace(".", " ").split())


# ---------------------------------------------------------------------------
# Pasul 0 - descoperire
# ---------------------------------------------------------------------------


def discover() -> None:
    """Listeaza TOATE competitiile de pe FBref, cu numele exact asteptat in league_dict.json."""
    import soccerdata as sd

    fb = sd.FBref(leagues="ENG-Premier League", seasons=SEASON)
    fb.read_leagues()  # forteaza descarcarea si punerea in cache a /en/comps/

    cache = Path.home() / "soccerdata" / "data" / "FBref" / "leagues.html"
    if not cache.exists():
        sys.exit(f"Nu gasesc {cache}. Verifica instalarea Chrome / conexiunea.")

    tables = pd.read_html(cache)
    names = sorted({str(n) for t in tables if "Competition Name" in t.columns
                    for n in t["Competition Name"].dropna()})
    print(f"{len(names)} competitii disponibile pe FBref:\n")
    for n in names:
        print(" ", n)
    print("\nCopiaza numele EXACTE de mai sus in ~/soccerdata/config/league_dict.json, "
          "campul \"FBref\". Ce nu apare aici, FBref nu acopera "
          "(probabil Slovacia si Azerbaidjan -> ramai pe Transfermarkt pentru "
          "Slovan Bratislava si Sabah).")


# ---------------------------------------------------------------------------
# Pasul 1 - extragere
# ---------------------------------------------------------------------------


def _read(leagues: list[str], season: str) -> pd.DataFrame:
    import soccerdata as sd

    frames = []
    for lg in leagues:
        try:
            fb = sd.FBref(leagues=lg, seasons=season)
            df = fb.read_player_season_stats(stat_type="standard").reset_index()
            df["source_league"] = lg
            frames.append(df)
            print(f"  OK  {lg:<36} {len(df):>5} randuri")
        except Exception as exc:  # liga neacoperita / nume gresit / blocare
            print(f"  --  {lg:<36} {type(exc).__name__}: {exc}", file=sys.stderr)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def extract() -> None:
    OUT.mkdir(exist_ok=True)
    print("Descarc statisticile de jucator (FBref rate-limiteaza agresiv, ai rabdare)...")
    df = pd.concat(
        [_read(LEAGUES, SEASON), _read(LEAGUES_SINGLE_YEAR, SEASON_SINGLE_YEAR)],
        ignore_index=True,
    )
    if df.empty:
        sys.exit("Nu s-a descarcat nimic.")

    # Aplatizez coloanele multi-index si pastrez ce ne trebuie.
    df.columns = ["_".join([str(c) for c in col if c and c != "Unnamed: 0_level_0"]).strip("_")
                  if isinstance(col, tuple) else str(col) for col in df.columns]

    keep = {}
    for col in df.columns:
        low = col.lower()
        if low in ("league", "season", "team", "player", "nation", "pos", "age",
                   "source_league"):
            keep[col] = low
        elif low.endswith("playing_time_min") or low == "min":
            keep[col] = "minutes"
        elif low.endswith("playing_time_90s") or low == "90s":
            keep[col] = "nineties"
        elif low.endswith("playing_time_starts") or low == "starts":
            keep[col] = "starts"

    out = df[list(keep)].rename(columns=keep)
    out["minutes"] = pd.to_numeric(out.get("minutes"), errors="coerce").fillna(0).astype(int)
    out["pos"] = out["pos"].fillna("")

    # Agregare pe jucator: minute totale + posturi ordonate dupa minute.
    def agg(g: pd.DataFrame) -> pd.Series:
        pos_min: dict[str, int] = {}
        for _, r in g.iterrows():
            for p in str(r["pos"]).split(","):
                p = p.strip()
                if p:
                    pos_min[p] = pos_min.get(p, 0) + int(r["minutes"])
        ordered = sorted(pos_min, key=lambda k: -pos_min[k])
        cl = g.loc[g["league"].astype(str).str.contains("Champions", case=False, na=False),
                   "minutes"].sum()
        return pd.Series({
            "echipe": " / ".join(sorted(set(g["team"].astype(str)))),
            "minute_total": int(g["minutes"].sum()),
            "minute_cl": int(cl),
            "minute_campionat": int(g["minutes"].sum() - cl),
            "posturi_jucate": " / ".join(ordered),
            "post_principal_fbref": ordered[0] if ordered else "",
            "detaliu_minute_post": " | ".join(f"{k}:{v}" for k, v in
                                              sorted(pos_min.items(), key=lambda x: -x[1])),
        })

    res = out.groupby("player", as_index=False, group_keys=False).apply(agg).reset_index()
    res = res.rename(columns={"player": "nume"})
    res["key"] = res["nume"].map(norm)
    res.to_csv(OUT / "fbref_2526_players.csv", index=False, encoding="utf-8-sig")
    print(f"\nScris: {OUT / 'fbref_2526_players.csv'} ({len(res)} jucatori)")


# ---------------------------------------------------------------------------
# Pasul 2 - imbinare cu Transfermarkt
# ---------------------------------------------------------------------------


def merge(tm_csv: str) -> None:
    tm = pd.read_csv(tm_csv)
    fb = pd.read_csv(OUT / "fbref_2526_players.csv")
    tm["key"] = tm["nume"].map(norm)

    m = tm.merge(fb.drop(columns=["echipe"], errors="ignore"),
                 on="key", how="left", suffixes=("", "_fb"))
    m["acoperire"] = m["posturi_jucate"].notna().map({True: "FBref+TM", False: "doar TM"})

    cols = ["echipa", "nume", "posturi", "post_principal_fbref", "posturi_jucate",
            "minute_campionat", "minute_cl", "minute_total", "detaliu_minute_post",
            "acoperire"]
    m = m[[c for c in cols if c in m.columns]]
    m.to_csv(OUT / "ucl_squads_enriched.csv", index=False, encoding="utf-8-sig")

    total = len(m)
    hit = (m["acoperire"] == "FBref+TM").sum()
    print(f"Scris: {OUT / 'ucl_squads_enriched.csv'}")
    print(f"Potrivire FBref: {hit}/{total} ({hit / total:.0%})")
    print("\nEchipe cu acoperire slaba (verifica manual):")
    weak = (m.groupby("echipa")["acoperire"]
              .apply(lambda s: (s == "doar TM").mean())
              .sort_values(ascending=False))
    print(weak[weak > 0.3].to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--merge", metavar="TM_CSV")
    a = ap.parse_args()
    if a.discover:
        discover()
    elif a.extract:
        extract()
    elif a.merge:
        merge(a.merge)
    else:
        ap.print_help()
