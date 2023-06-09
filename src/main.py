import datetime
import json
import os
import re
import sys
from typing import Any, Dict

import holidays
import pytz
from dateutil import parser
from dateutil.rrule import FR, MO, SA, SU, TH, TU, WE
from github import Github


def post_comment_on_pr(
    github_token: str, pr_number: int, message: str, check_existing_comment: bool
) -> None:
    gh = Github(github_token)
    repo = gh.get_repo(os.environ["GITHUB_REPOSITORY"])
    pr = repo.get_pull(pr_number)
    comments = pr.get_issue_comments()

    existing_comment_id = None
    if check_existing_comment:
        for comment in comments:
            if comment.body == message:
                existing_comment_id = comment.id
                break

    if not existing_comment_id:
        pr.create_issue_comment(message)


def validate_timezone(timezone: str) -> None:
    if timezone not in pytz.all_timezones:
        raise ValueError(
            f"Invalid timezone: {timezone}. Please provide a valid timezone."
        )


def is_holiday(now, holidays_config):
    if not holidays_config:
        return False

    country_holidays = holidays.country_holidays(
        country=holidays_config["country"], subdiv=holidays_config.get("state", None)
    )

    return now.date() in country_holidays


def validate_restricted_times(restricted_times: Dict[str, Any]) -> None:
    if "weekly" not in restricted_times:
        raise ValueError("Missing 'weekly' key in restricted_times dictionary.")

    valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    for rule in restricted_times["weekly"]:
        if not set(rule["days"]).issubset(valid_days):
            raise ValueError(
                "Invalid day keys in the restricted_times dictionary. Use "
                + "'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'."
            )

        if not isinstance(rule["intervals"], list):
            raise ValueError(
                f"Invalid value for '{rule['days']}' in restricted_times."
                + " It should be a list of tuples."
            )
        for interval in rule["intervals"]:
            if not isinstance(interval, list) or len(interval) != 2:
                raise ValueError(
                    f"Invalid interval '{interval}' for '{rule['days']}'"
                    + " in restricted_times. It should be a tuple with two numbers."
                )
            if interval[1] <= interval[0]:
                raise ValueError(
                    f"Invalid interval '{interval}' for '{rule['days']}'"
                    + " in restricted_times. The second number should"
                    + " be greater than the first."
                )


def validate_custom_message(custom_message: str) -> None:
    if not custom_message.strip():
        raise ValueError("Custom message cannot be an empty string.")


def is_restricted_time(timezone, restricted_times, now=None):
    tz = pytz.timezone(timezone)
    if now is None:
        now = datetime.datetime.now(tz)
    now = now.astimezone(tz)

    for rule in restricted_times["weekly"]:
        day_map = {
            "mon": MO,
            "tue": TU,
            "wed": WE,
            "thu": TH,
            "fri": FR,
            "sat": SA,
            "sun": SU,
        }
        mapped_days = [day_map[d] for d in rule["days"]]
        if now.weekday() in [d.weekday for d in mapped_days] and any(
            start <= now.hour + now.minute / 60 < end
            for start, end in rule["intervals"]
        ):
            return True

    for rule in restricted_times["dates"]:
        date = parser.parse(rule["date"]).date()
        if now.date() == date and any(
            start <= now.hour + now.minute / 60 < end
            for start, end in rule["intervals"]
        ):
            return True

    holiday_intervals = restricted_times.get("holidays", {}).get("intervals", [])
    return bool(
        is_holiday(now, restricted_times.get("holidays"))
        and any(
            start <= now.hour + now.minute / 60 < end
            for start, end in holiday_intervals
        )
    )


def parse_pull_request_id(github_ref):
    pattern = r"refs/pull/(\d+)/merge"
    if match := re.search(pattern, github_ref):
        return int(match[1])
    else:
        raise ValueError(f"Invalid GitHub ref: {github_ref}")


def main():
    # Set Defaults
    restricted_times_default = {
        "weekly": [
            {
                "days": ["mon", "tue", "wed", "thu", "fri"],
                "intervals": [[0, 7], [16.5, 24]],
            }
        ],
        "dates": [{"date": "2023-12-25", "intervals": [[0, 24]]}],
        "holidays": {"country": "AU", "state": "NSW", "intervals": [[0, 24]]},
    }

    # Get the inputs from the environment
    github_token = os.environ["INPUT_GITHUB_TOKEN"]
    pr_title = os.environ["INPUT_PR_TITLE"]
    timezone = os.environ.get("INPUT_TIMEZONE", "Australia/Sydney")
    restricted_times_json = os.environ.get(
        "INPUT_RESTRICTED_TIMES", json.dumps(restricted_times_default)
    )
    custom_message = os.environ.get(
        "INPUT_CUSTOM_MESSAGE",
        "⚠️ **PR merging is not allowed outside business hours.** ⚠️",
    )
    check_existing_comment = (
        os.environ.get("INPUT_CHECK_EXISTING_COMMENT", "true").lower() == "true"
    )

    # Validate the inputs
    validate_timezone(timezone)
    try:
        # print("Restricted Times:\n")
        # print(restricted_times_json)
        restricted_times = json.loads(restricted_times_json)
        validate_restricted_times(restricted_times)
    except ValueError as e:
        raise ValueError(
            f"❌ Error parsing RESTRICTED_TIMES {restricted_times_json}"
        ) from e
    validate_custom_message(custom_message)

    if not is_restricted_time(timezone, restricted_times):
        print("✅ Merging is allowed at this time.")
        sys.exit(0)

    if re.search(r"(?i)hotfix\s*:", pr_title):
        print("✅ Hotfix PRs are allowed to merge outside business hours.")
        sys.exit(0)

    print("❌ Merging is not allowed during the specified time.")

    # Logic to post to Github and handle existing comments
    github_ref = os.environ["GITHUB_REF"]
    pr_number = parse_pull_request_id(github_ref)
    post_comment_on_pr(github_token, pr_number, custom_message, check_existing_comment)
    sys.exit(1)


if __name__ == "__main__":
    main()
