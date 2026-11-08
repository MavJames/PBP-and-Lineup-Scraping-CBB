import argparse
import subprocess
import sys
from pathlib import Path

from mbb_pbp import download_game


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download a game's ESPN play-by-play and calculate lineups."
    )
    parser.add_argument("game_id", type=int, help="ESPN game ID")
    parser.add_argument(
        "--home-starters",
        nargs=5,
        metavar="ATHLETE_ID",
        help="Override automatic home-team starter detection",
    )
    parser.add_argument(
        "--away-starters",
        nargs=5,
        metavar="ATHLETE_ID",
        help="Override automatic away-team starter detection",
    )
    args = parser.parse_args()

    output_dir = Path.cwd()
    pbp_path = download_game(args.game_id, output_dir)
    if pbp_path is None:
        return 1

    script_dir = Path(__file__).resolve().parent
    command = [
        sys.executable,
        str(script_dir / "get_lineups.py"),
        str(pbp_path),
    ]
    if args.home_starters:
        command.extend(["--home-starters", *args.home_starters])
    if args.away_starters:
        command.extend(["--away-starters", *args.away_starters])

    subprocess.run(command, check=True)
    print("Play-by-play and lineup processing complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
