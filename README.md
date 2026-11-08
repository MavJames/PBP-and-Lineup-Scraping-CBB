# ESPN College Basketball Lineups

Download ESPN men's college basketball play-by-play and build event-level
lineups and lineup summaries from substitutions.

Possessions are inferred from the play-by-play event sequence, so possession
counts and ratings may differ from official statistics.

## Requirements

- Python 3.10 or newer
- `sportsdataverse`
- `polars`

Install the dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Run a game

Use the ESPN game ID:

```bash
python3 run_game.py 401926862
```

The three matchup-named CSVs are written to the current directory:

- `mbb_pbp_<away>_at_<home>.csv` — cleaned ESPN play-by-play
- `lineup_events_<away>_at_<home>.csv` — each play with the inferred lineup and possession
- `lineup_summary_<away>_at_<home>.csv` — lineup minutes, basic stats, possession counts, and ratings

If automatic starter detection does not work for a game, provide the five ESPN
athlete IDs for each team:

```bash
python3 run_game.py GAME_ID \
  --home-starters ID1 ID2 ID3 ID4 ID5 \
  --away-starters ID1 ID2 ID3 ID4 ID5
```

Run an individual step if needed:

```bash
python3 mbb_pbp.py GAME_ID
python3 get_lineups.py mbb_pbp_<away>_at_<home>.csv
```

## Notes

- Possessions are estimated from event sequences, including turnovers,
  defensive rebounds, made baskets, and free-throw sequences. Review results
  for games with unusual or incomplete play-by-play.
- `ORTG` and `DRTG` are based on inferred possession counts and should be
  treated as estimates.
- Generated game CSVs are excluded from this repository; running the scripts
  creates them locally.
