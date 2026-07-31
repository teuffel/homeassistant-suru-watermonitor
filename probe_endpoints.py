"""Probe the SURU API for the consumption/reading endpoints the app uses.

Logs in like the integration does, then tries the known `consumption` and
`reading` endpoints with every plausible combination of query params
(range, groupBy, startDate) so you can see what daily / weekly / monthly /
hourly data is available.

Usage:
    python probe_endpoints.py --email you@x.com --password secret
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent
        / "custom_components" / "suru_water_monitor"),
)

import aiohttp  # noqa: E402

from api import SuruWaterApi  # noqa: E402


def _short(obj, limit=400):
    text = json.dumps(obj, default=str)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars total)"


async def _try(label, coro):
    try:
        result = await coro
        status = f"200 OK  {len(json.dumps(result, default=str))} bytes"
    except Exception as err:  # noqa: BLE001 - probing, we want every failure too
        result = None
        status = f"FAIL    {err}"
    print(f"  {label:<72} {status}")
    if result:
        print(f"      {_short(result)}")
    return result


def _start(day: str) -> str:
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _probe_consumption(api: SuruWaterApi, unit_id: str) -> None:
    print(f"\n[consumption] units/{unit_id}/consumption  (known: range=day,startDate=now)")
    now = datetime.now(timezone.utc)
    days_ago = lambda d: (now - timedelta(days=d)).replace(  # noqa: E731
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    combos = [
        ("range=hour", {"range": "hour", "startDate": _start("now")}),
        ("range=week", {"range": "week", "startDate": _start("now")}),
        ("range=month", {"range": "month", "startDate": _start("now")}),
        ("range=year", {"range": "year", "startDate": _start("now")}),
        ("range=day+groupBy=hour", {"range": "day", "groupBy": "hour", "startDate": _start("now")}),
        ("range=day+groupBy=day", {"range": "day", "groupBy": "day", "startDate": _start("now")}),
        ("range=week+groupBy=hour", {"range": "week", "groupBy": "hour", "startDate": _start("now")}),
        ("range=week+groupBy=day", {"range": "week", "groupBy": "day", "startDate": _start("now")}),
        ("range=month+groupBy=day", {"range": "month", "groupBy": "day", "startDate": _start("now")}),
        ("range=year+groupBy=month", {"range": "year", "groupBy": "month", "startDate": _start("now")}),
        ("no startDate", {"range": "day"}),
        ("startDate=-30d (default groupBy)", {"range": "day", "startDate": days_ago(30)}),
    ]
    for label, params in combos:
        await _try(f"GET consumption  {label}", api._get(f"units/{unit_id}/consumption", params))


async def _probe_reading(api: SuruWaterApi, unit_id: str) -> None:
    print(f"\n[reading] units/{unit_id}/reading  (known: date=now)")
    now = datetime.now(timezone.utc)
    for offset in (0, 1, 7):
        stamp = (now - timedelta(days=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await _try(f"GET reading date=-{offset}d", api._get(f"units/{unit_id}/reading", {"date": stamp}))
    for extra in ({"includeHistory": "true"}, {"from": "2026-01-01"}, {"unit": "l"}):
        params = {"date": now.strftime("%Y-%m-%dT%H:%M:%SZ"), **extra}
        await _try(f"GET reading {extra}", api._get(f"units/{unit_id}/reading", params))


async def _probe_guesses(api: SuruWaterApi, unit_id: str) -> None:
    print(f"\n[guesses] other endpoint paths that might exist on the API Gateway")
    paths = [
        "units",
        f"units/{unit_id}/measurements",
        f"units/{unit_id}/water",
        f"units/{unit_id}/statistics",
        f"units/{unit_id}/history",
        f"units/{unit_id}/devices",
        f"units/{unit_id}/consumption/total",
        f"units/{unit_id}/reading/history",
        f"devices",
        f"devices/{unit_id}",
        "profile",
        "user",
        "users/me",
    ]
    for path in paths:
        await _try(f"GET /{path}", api._get(path))


async def _run(args: argparse.Namespace) -> None:
    email = args.email or os.environ.get("SURU_EMAIL")
    password = args.password or os.environ.get("SURU_PASSWORD")
    if not email or not password:
        raise SystemExit("Provide credentials via --email/--password or SURU_EMAIL/SURU_PASSWORD env vars.")

    async with aiohttp.ClientSession() as session:
        api = SuruWaterApi(session, email, password)
        await api.async_login()
        units = await api.async_get_units()
        if not units:
            raise SystemExit("No water units on this account.")
        unit_id = units[0]["id"]
        print(f"Probing with unit id: {unit_id}")

        await _probe_consumption(api, unit_id)
        await _probe_reading(api, unit_id)
        await _probe_guesses(api, unit_id)
        print("\nDone. Note which 200 OK responses return useful data.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe SURU API endpoints.")
    parser.add_argument("--email", help="SURU account email (default: SURU_EMAIL)")
    parser.add_argument("--password", help="SURU account password (default: SURU_PASSWORD)")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_run(_parse_args()))
