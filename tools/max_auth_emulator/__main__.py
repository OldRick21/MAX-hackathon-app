"""Command-line interface for generating test MAX authentication data."""

from __future__ import annotations

import argparse
import os
import sys

from .signer import build_launch_url, generate_init_data


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", required=True, type=positive_integer, help="MAX user.id")
    parser.add_argument("--first-name", help="optional signed first_name")
    parser.add_argument("--last-name", help="optional signed last_name")
    parser.add_argument("--username", help="optional signed MAX username")
    parser.add_argument("--query-id", help="query_id; random UUID by default")
    parser.add_argument("--auth-date", type=int, help="Unix timestamp; current time by default")
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="read the bot token from stdin instead of MAX_BOT_TOKEN/BOT_TOKEN",
    )


def bot_token(read_stdin: bool) -> str:
    if read_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    else:
        value = os.environ.get("MAX_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""
    if not value:
        source = "stdin" if read_stdin else "MAX_BOT_TOKEN or BOT_TOKEN"
        raise ValueError(f"bot token is missing in {source}")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m tools.max_auth_emulator",
        description="Generate signed MAX WebApp initData for controlled testing.",
    )
    commands = result.add_subparsers(dest="command", required=True)

    raw = commands.add_parser("init-data", help="print raw signed initData")
    add_identity_arguments(raw)

    url = commands.add_parser("url", help="print a frontend launch URL with #WebAppData")
    add_identity_arguments(url)
    url.add_argument("--base-url", required=True, help="frontend URL, for example https://example.org/")
    url.add_argument("--platform", default="web", help="WebAppPlatform value (default: web)")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        init_data = generate_init_data(
            bot_token(arguments.token_stdin),
            arguments.user_id,
            auth_date=arguments.auth_date,
            query_id=arguments.query_id,
            first_name=arguments.first_name,
            last_name=arguments.last_name,
            username=arguments.username,
        )
        output = (
            build_launch_url(arguments.base_url, init_data, platform=arguments.platform)
            if arguments.command == "url"
            else init_data
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
