import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


SUBSTITUTION_RE = re.compile(r"^(?P<name>.+?)\s+subbing\s+(?P<action>in|out)\s+for\b", re.I)
PLAYER_COLUMNS = [f"P{i}" for i in range(1, 6)]
STAT_COLUMNS = [
    "minutes",
    "offense_minutes",
    "defense_minutes",
    "points_for",
    "points_against",
    "off_possessions",
    "def_possessions",
    "ePOSS",
    "ORTG",
    "DRTG",
    "NETRTG",
    "TimePerPoss",
    "dTimePerPoss",
    "FGA",
    "FGM",
    "3PA",
    "3PM",
    "FTA",
    "FTM",
    "ORB",
    "DRB",
    "AST",
    "TO",
    "BLK",
    "opp_FGA",
    "opp_FGM",
    "opp_3PA",
    "opp_3PM",
    "opp_FTA",
    "opp_FTM",
    "opp_ORB",
    "opp_DRB",
    "opp_AST",
    "opp_TO",
    "opp_BLK",
]


def parse_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def filename_part(team_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", team_name).strip("_")


def is_true(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"true", "1", "yes"}


def infer_starters(
    rows: list[dict[str, str]], team_id: str, supplied: list[str] | None
) -> list[str]:
    if supplied is not None:
        starters = supplied
    else:
        first_period = min(int(row["period_number"]) for row in rows)
        seen_entering: set[str] = set()
        starters = []
        for row in rows:
            if int(row["period_number"]) != first_period or row["team.id"] != team_id:
                continue
            match = SUBSTITUTION_RE.match(row.get("text", "").strip())
            player_id = row.get("participants.0.athlete.id", "").strip()
            if match is None or not player_id:
                continue
            if match.group("action").lower() == "out":
                if player_id not in seen_entering and player_id not in starters:
                    starters.append(player_id)
            else:
                seen_entering.add(player_id)

    if len(starters) != 5 or len(set(starters)) != 5:
        raise ValueError(
            f"Could not identify exactly five starters for team {team_id}; "
            "rerun with --home-starters or --away-starters and five athlete IDs."
        )
    return list(starters)


def event_clock_remaining(row: dict[str, str]) -> int | None:
    value = parse_int(row.get("start.period_seconds_remaining"))
    if value is not None:
        return value
    clock = row.get("clock_display_value", "")
    match = re.fullmatch(r"(\d+):(\d{2})", clock.strip())
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def lineup_key(team_id: str, players: list[str]) -> tuple[str, ...]:
    if len(players) != 5 or len(set(players)) != 5:
        raise ValueError(f"Invalid lineup for team {team_id}: {players}")
    return (team_id, *sorted(players))


def update_lineup(
    lineups: dict[str, list[str]],
    team_id: str,
    outgoing: str,
    incoming: str,
    game_play_number: str,
) -> None:
    active = lineups[team_id]
    if outgoing not in active:
        raise ValueError(
            f"Substitution at play {game_play_number} removes athlete {outgoing}, "
            f"who is not in team {team_id}'s lineup."
        )
    if incoming in active:
        raise ValueError(
            f"Substitution at play {game_play_number} adds athlete {incoming}, "
            f"who is already in team {team_id}'s lineup."
        )
    active[active.index(outgoing)] = incoming


def stat_event(row: dict[str, str]) -> dict[str, int]:
    points_attempted = parse_int(row.get("pointsAttempted")) or 0
    shooting_play = is_true(row.get("shootingPlay"))
    made = is_true(row.get("scoringPlay"))
    event_type = row.get("type.text", "").lower()
    has_assist = bool(row.get("participants.1.athlete.id", "").strip())

    return {
        "FGA": int(shooting_play and points_attempted in (2, 3)),
        "FGM": int(shooting_play and points_attempted in (2, 3) and made),
        "3PA": int(shooting_play and points_attempted == 3),
        "3PM": int(shooting_play and points_attempted == 3 and made),
        "FTA": int(shooting_play and points_attempted == 1),
        "FTM": int(shooting_play and points_attempted == 1 and made),
        "ORB": int("offensive rebound" in event_type),
        "DRB": int("defensive rebound" in event_type),
        "AST": int(made and has_assist),
        "TO": int("turnover" in event_type),
        "BLK": int("block" in event_type),
    }


def is_free_throw(row: dict[str, str]) -> bool:
    return "freethrow" in row.get("type.text", "").lower()


def has_and_one(rows: list[dict[str, str]], index: int) -> bool:
    shot = rows[index]
    shooter_id = shot.get("participants.0.athlete.id", "").strip()
    team_id = shot.get("team.id", "")
    shot_remaining = event_clock_remaining(shot)
    if not shooter_id or shot_remaining is None:
        return False

    for candidate in rows[index + 1 :]:
        if candidate["period_number"] != shot["period_number"]:
            break
        remaining = event_clock_remaining(candidate)
        if remaining is None or shot_remaining - remaining > 5:
            break
        if (
            is_free_throw(candidate)
            and candidate.get("team.id") == team_id
            and candidate.get("participants.0.athlete.id", "").strip() == shooter_id
        ):
            return True
        event_type = candidate.get("type.text", "").lower()
        if (
            is_true(candidate.get("shootingPlay"))
            or "rebound" in event_type
            or "turnover" in event_type
        ):
            break
    return False


def starting_possession_team(
    period_rows: list[dict[str, str]],
    home_id: str,
    away_id: str,
) -> str:
    for row in period_rows:
        if row.get("type.text") == "Jumpball":
            match = re.search(r"jump ball won by (.+)$", row.get("text", ""), re.I)
            if match:
                winner = match.group(1).strip().lower()
                for team_id, team_name in ((home_id, period_rows[0]["homeTeamName"]),
                                           (away_id, period_rows[0]["awayTeamName"])):
                    if winner == team_name.strip().lower():
                        return team_id

    for row in period_rows:
        if row.get("type.text") == "Substitution":
            continue
        team_id = row.get("team.id", "")
        if team_id not in {home_id, away_id}:
            continue
        if is_true(row.get("shootingPlay")) or "turnover" in row.get(
            "type.text", ""
        ).lower():
            return team_id

    for row in period_rows:
        team_id = row.get("team.id", "")
        if team_id in {home_id, away_id} and row.get("type.text") != "Substitution":
            return team_id
    raise ValueError(f"Could not determine the starting possession for period {period_rows[0]['period_number']}.")


def assign_possessions(
    rows: list[dict[str, str]], home_id: str, away_id: str
) -> list[dict[str, Any]]:
    periods: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        periods[row["period_number"]].append(index)

    possession_rows: list[dict[str, Any] | None] = [None] * len(rows)
    possession_number = 0
    for indices in periods.values():
        period_rows = [rows[index] for index in indices]
        possession_team = starting_possession_team(period_rows, home_id, away_id)
        possession_number += 1
        switch_pending = False
        pending_clock: int | None = None

        for position, row_index in enumerate(indices):
            row = rows[row_index]
            remaining = event_clock_remaining(row)
            if (
                switch_pending
                and remaining is not None
                and pending_clock is not None
                and remaining != pending_clock
            ):
                possession_team = away_id if possession_team == home_id else home_id
                possession_number += 1
                switch_pending = False

            event_team = row.get("team.id", "")
            event_type = row.get("type.text", "").lower()
            team_mismatch = (
                event_team in {home_id, away_id}
                and event_team != possession_team
                and (
                    is_true(row.get("shootingPlay"))
                    or "turnover" in event_type
                )
            )
            if team_mismatch:
                possession_team = away_id if possession_team == home_id else home_id
                possession_number += 1
                switch_pending = False

            possession_rows[row_index] = {
                "Poss_Num": possession_number,
                "Poss_Team": possession_team,
            }

            next_row = rows[indices[position + 1]] if position + 1 < len(indices) else None
            made_field_goal = (
                is_true(row.get("shootingPlay"))
                and (parse_int(row.get("pointsAttempted")) or 0) in (2, 3)
                and is_true(row.get("scoringPlay"))
            )
            made_free_throw = is_free_throw(row) and is_true(row.get("scoringPlay"))
            next_is_free_throw_or_defensive_rebound = (
                next_row is not None
                and (
                    is_free_throw(next_row)
                    or "defensive rebound"
                    in next_row.get("type.text", "").lower()
                )
            )
            ends_possession = (
                "defensive rebound" in event_type
                or "turnover" in event_type
                or (made_field_goal and not has_and_one(rows, row_index))
                or (
                    made_free_throw
                    and not next_is_free_throw_or_defensive_rebound
                )
            )
            if ends_possession:
                switch_pending = True
                pending_clock = remaining

    if any(row is None for row in possession_rows):
        raise RuntimeError("Failed to assign possession data to every play.")
    return [row for row in possession_rows if row is not None]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build lineup events and lineup-level stats from an ESPN PBP CSV."
    )
    parser.add_argument("csv_file", type=Path, help="ESPN play-by-play CSV")
    parser.add_argument(
        "--home-starters",
        nargs=5,
        metavar="ATHLETE_ID",
        help="Override automatic starter detection with five athlete IDs",
    )
    parser.add_argument(
        "--away-starters",
        nargs=5,
        metavar="ATHLETE_ID",
        help="Override automatic starter detection with five athlete IDs",
    )
    args = parser.parse_args()

    with args.csv_file.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fields = set(reader.fieldnames or [])

    required_fields = {
        "game_play_number",
        "period_number",
        "type.text",
        "text",
        "team.id",
        "participants.0.athlete.id",
        "homeTeamId",
        "awayTeamId",
        "homeTeamName",
        "awayTeamName",
        "home_score",
        "away_score",
    }
    missing_fields = sorted(required_fields - fields)
    if missing_fields:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing_fields)}")
    if not rows:
        raise ValueError("The play-by-play CSV has no rows.")

    rows.sort(key=lambda row: int(row["game_play_number"]))
    home_id = rows[0]["homeTeamId"]
    away_id = rows[0]["awayTeamId"]
    home_name = rows[0]["homeTeamName"]
    away_name = rows[0]["awayTeamName"]
    if not home_id or not away_id:
        raise ValueError("The CSV is missing home or away team IDs.")

    lineups = {
        home_id: infer_starters(rows, home_id, args.home_starters),
        away_id: infer_starters(rows, away_id, args.away_starters),
    }
    possession_rows = assign_possessions(rows, home_id, away_id)
    team_names = {home_id: home_name, away_id: away_name}
    player_names: dict[str, str] = {}
    pending_out: dict[str, list[str]] = defaultdict(list)
    pending_in: dict[str, list[str]] = defaultdict(list)
    summaries: dict[tuple[str, ...], dict[str, Any]] = {}
    possession_sets: dict[tuple[str, ...], dict[str, set[tuple[str, int]]]] = {}
    event_output: list[dict[str, Any]] = []
    previous_remaining: int | None = None
    previous_period: str | None = None
    previous_home_score = 0
    previous_away_score = 0

    def summary_for(team_id: str, players: list[str]) -> dict[str, Any]:
        key = lineup_key(team_id, players)
        if key not in summaries:
            summaries[key] = {column: 0 for column in STAT_COLUMNS}
            possession_sets[key] = {"off": set(), "def": set()}
        return summaries[key]

    def add_interval(seconds: int, possession_team: str) -> None:
        if seconds <= 0:
            return
        for team_id in (home_id, away_id):
            summary = summary_for(team_id, lineups[team_id])
            summary["minutes"] += seconds / 60
            summary[
                "offense_minutes" if team_id == possession_team else "defense_minutes"
            ] += seconds / 60

    for row_index, row in enumerate(rows):
        period = row["period_number"]
        remaining = event_clock_remaining(row)
        if period == previous_period and remaining is not None and previous_remaining is not None:
            add_interval(
                max(0, previous_remaining - remaining),
                possession_rows[row_index]["Poss_Team"],
            )

        event_type = row.get("type.text", "")
        possession = possession_rows[row_index]
        possession_key = (period, possession["Poss_Num"])
        if event_type != "Substitution":
            for team_id in (home_id, away_id):
                key = lineup_key(team_id, lineups[team_id])
                summary_for(team_id, lineups[team_id])
                possession_sets[key][
                    "off" if team_id == possession["Poss_Team"] else "def"
                ].add(possession_key)

        substitution = SUBSTITUTION_RE.match(row.get("text", "").strip())
        if event_type == "Substitution":
            if substitution is None:
                raise ValueError(
                    f"Unrecognized substitution at play {row['game_play_number']}: "
                    f"{row.get('text', '')}"
                )
            team_id = row.get("team.id", "")
            player_id = row.get("participants.0.athlete.id", "").strip()
            if team_id not in lineups or not player_id:
                raise ValueError(
                    f"Substitution at play {row['game_play_number']} is missing "
                    "a valid team or athlete ID."
                )
            player_names[player_id] = substitution.group("name").strip()
            action = substitution.group("action").lower()
            if action == "out":
                pending_out[team_id].append(player_id)
            else:
                pending_in[team_id].append(player_id)

            while pending_out[team_id] and pending_in[team_id]:
                outgoing = pending_out[team_id].pop(0)
                incoming = pending_in[team_id].pop(0)
                update_lineup(
                    lineups,
                    team_id,
                    outgoing,
                    incoming,
                    row["game_play_number"],
                )
        else:
            home_score = parse_int(row.get("home_score")) or 0
            away_score = parse_int(row.get("away_score")) or 0
            points_home = max(0, home_score - previous_home_score)
            points_away = max(0, away_score - previous_away_score)
            previous_home_score = home_score
            previous_away_score = away_score

            home_summary = summary_for(home_id, lineups[home_id])
            away_summary = summary_for(away_id, lineups[away_id])
            home_summary["points_for"] += points_home
            home_summary["points_against"] += points_away
            away_summary["points_for"] += points_away
            away_summary["points_against"] += points_home

            event_team = row.get("team.id", "")
            if event_team in lineups:
                stats = stat_event(row)
                own_summary, opponent_summary = (
                    (home_summary, away_summary)
                    if event_team == home_id
                    else (away_summary, home_summary)
                )
                for stat, count in stats.items():
                    own_summary[stat] += count
                    opponent_summary[f"opp_{stat}"] += count

        output_row: dict[str, Any] = {
            "game_play_number": row["game_play_number"],
            "period_number": period,
            "clock_display_value": row.get("clock_display_value", ""),
            "type.text": event_type,
            "text": row.get("text", ""),
            "team.id": row.get("team.id", ""),
            "home_score": row.get("home_score", ""),
            "away_score": row.get("away_score", ""),
            "Poss_Num": possession["Poss_Num"],
            "Poss_Team": team_names.get(possession["Poss_Team"], possession["Poss_Team"]),
        }
        for side, team_id in (("home", home_id), ("away", away_id)):
            for index, player_id in enumerate(sorted(lineups[team_id]), start=1):
                output_row[f"{side}_P{index}_id"] = player_id
                output_row[f"{side}_P{index}"] = player_names.get(
                    player_id, f"Player {player_id}"
                )
        event_output.append(output_row)

        previous_remaining = remaining
        previous_period = period

    unmatched = {
        team_id: {"out": pending_out[team_id], "in": pending_in[team_id]}
        for team_id in lineups
        if pending_out[team_id] or pending_in[team_id]
    }
    if unmatched:
        raise ValueError(f"Unmatched substitution events remain: {unmatched}")

    for output_row in event_output:
        for side in ("home", "away"):
            for index in range(1, 6):
                player_id = output_row[f"{side}_P{index}_id"]
                output_row[f"{side}_P{index}"] = player_names.get(
                    player_id, f"Player {player_id}"
                )

    matchup = f"{filename_part(away_name)}_at_{filename_part(home_name)}"
    events_path = args.csv_file.with_name(f"lineup_events_{matchup}.csv")
    summary_path = args.csv_file.with_name(f"lineup_summary_{matchup}.csv")

    with events_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(event_output[0]))
        writer.writeheader()
        writer.writerows(event_output)

    summary_columns = (
        ["Team", *PLAYER_COLUMNS, *[f"{column}_id" for column in PLAYER_COLUMNS]]
        + STAT_COLUMNS
        + ["plus_minus"]
    )
    with summary_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=summary_columns)
        writer.writeheader()
        for key, stats in sorted(summaries.items()):
            team_id, *players = key
            off_possessions = len(possession_sets[key]["off"])
            def_possessions = len(possession_sets[key]["def"])
            stats["off_possessions"] = off_possessions
            stats["def_possessions"] = def_possessions
            stats["ePOSS"] = (
                (
                    stats["FGA"]
                    + 0.475 * stats["FTA"]
                    - stats["ORB"]
                    + stats["TO"]
                )
                + (
                    stats["opp_FGA"]
                    + 0.475 * stats["opp_FTA"]
                    - stats["opp_ORB"]
                    + stats["opp_TO"]
                )
            ) / 2
            stats["ORTG"] = (
                stats["points_for"] / off_possessions * 100 if off_possessions else 0
            )
            stats["DRTG"] = (
                stats["points_against"] / def_possessions * 100
                if def_possessions
                else 0
            )
            stats["NETRTG"] = stats["ORTG"] - stats["DRTG"]
            stats["TimePerPoss"] = (
                stats["offense_minutes"] * 60 / off_possessions
                if off_possessions
                else 0
            )
            stats["dTimePerPoss"] = (
                stats["defense_minutes"] * 60 / def_possessions
                if def_possessions
                else 0
            )
            output = {
                "Team": team_names[team_id],
                "plus_minus": stats["points_for"] - stats["points_against"],
                **stats,
            }
            for index, player_id in enumerate(players, start=1):
                output[f"P{index}"] = player_names.get(player_id, f"Player {player_id}")
                output[f"P{index}_id"] = player_id
            writer.writerow(output)

    print(f"Saved event lineups to {events_path}")
    print(f"Saved lineup summary to {summary_path}")
    print(f"Processed {len(rows)} play-by-play rows for {home_name} vs. {away_name}.")
    print(
        "Possession counts and ratings are inferred from ESPN event sequences; "
        "they may differ from official statistics."
    )


if __name__ == "__main__":
    main()
