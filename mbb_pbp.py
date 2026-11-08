import argparse
import re
from pathlib import Path

import polars as pl
from sportsdataverse.mbb import espn_mbb_pbp


def filename_part(team_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", team_name).strip("_")


def download_game(game_id: int, output_dir: Path | None = None) -> Path | None:
    if output_dir is None:
        output_dir = Path.cwd()

    game = espn_mbb_pbp(game_id=game_id)
    plays = game.get("plays", [])

    if not plays:
        print("No play-by-play available for this game.")
        return None

    pbp = pl.DataFrame(plays).rename(
        {
            "period.number": "period_number",
            "clock.displayValue": "clock_display_value",
            "homeScore": "home_score",
            "awayScore": "away_score",
        }
    )
    first_play = plays[0]
    matchup = (
        f"{filename_part(first_play['awayTeamName'])}_at_"
        f"{filename_part(first_play['homeTeamName'])}"
    )
    output_path = output_dir / f"mbb_pbp_{matchup}.csv"
    pbp.write_csv(output_path)

    print(f"Saved {len(pbp)} plays to {output_path}")
    print(
        pbp.select(
            [
                "period_number",
                "clock_display_value",
                "text",
                "home_score",
                "away_score",
            ]
        ).head(10)
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download a college basketball game's ESPN play-by-play."
    )
    parser.add_argument("game_id", type=int, help="ESPN game ID")
    args = parser.parse_args()

    download_game(args.game_id)


if __name__ == "__main__":
    main()
