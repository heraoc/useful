# UCL squads & positions

Preia loturile echipelor din UEFA Champions League și postul principal /
posturile secundare ale fiecărui jucător, folosind API-ul open-source
[`felipeall/transfermarkt-api`](https://github.com/felipeall/transfermarkt-api)
(scraping structurat pe transfermarkt.com).

## Pornire API local

```bash
git clone https://github.com/felipeall/transfermarkt-api.git
cd transfermarkt-api
docker build -t transfermarkt-api .
docker run -d -p 8000:8000 transfermarkt-api
```

## Rulare

```bash
pip install -r requirements.txt
python ucl_squads_positions.py --season 2026
python ucl_squads_positions.py --season 2025   # loturile de sezonul trecut
```

## Ieșiri

- `out/ucl_<season>_squads.csv`
- `out/ucl_<season>_squads.md`
- `cache/` — răspunsuri brute de la API, ca să nu re-interoghezi la fiecare rulare
