"""Poll SURU WaterMonitor data locally, without Home Assistant.

Reuses the integration's own client (custom_components/suru_water_monitor/api.py),
which only needs `aiohttp`. Credentials come from the SURU_EMAIL / SURU_PASSWORD
env vars or from --email / --password.

Examples:
    python poll_suru.py
    python poll_suru.py --email you@x.com --password secret
    python poll_suru.py --interval 600 --raw         # poll every 10 min
    python poll_suru.py --history days=30            # per-day totals, last 30 days
    python poll_suru.py --history day=2026-07-01     # hourly buckets for one day
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

from api import SuruApiError, SuruAuthError, SuruWaterApi  # noqa: E402

import aiohttp  # noqa: E402


def _dt(value: str | None) -> str:
    if not value:
        return "-"
    return value


def _sensor(unit_id: str, values: dict) -> None:
    detail = values.get("detail") or {}
    reading = values.get("reading") or {}
    water = detail.get("waterProperties") or {}
    devices = detail.get("devices") or []
    incidents = values.get("incidents") or {}
    incident_states = (incidents.get("incidents") or {})

    active_states = {"OPEN", "CONFIRMED"}
    incident_types = [
        key for key, val in incident_states.items()
        if isinstance(val, dict) and val.get("state") in active_states
    ]
    alarm = bool((incidents.get("basicAlarm") or {}).get("active")) or bool(incident_types)

    print(f"  unit id ........ {unit_id}")
    print(f"  meter reading ... {reading.get('reading'):>8} m3   (measured {_dt(reading.get('measuredAt'))})")
    print(f"  consumption .... {values.get('consumption_today_l'):>8} L today")
    print(f"  water hardness . {water.get('waterHardness', '-'):>8} dH")
    print(f"  pH ............. {water.get('phValue', '-'):>8}")
    print(f"  alarm .......... {'ACTIVE' if alarm else 'ok':>8}"
          + (f"  ({', '.join(incident_types)})" if incident_types else ""))
    print(f"  devices ........ {[d.get('id') for d in devices]}")


def _print_report(data: dict[str, dict]) -> None:
    if not data:
        print("No water units found on this account.")
        return
    for unit_id, values in data.items():
        _sensor(unit_id, values)


async def _poll_once(api: SuruWaterApi) -> dict[str, dict]:
    units = await api.async_get_units()
    data: dict[str, dict] = {}
    for unit in units:
        unit_id = unit.get("id")
        if not unit_id:
            continue
        detail = await api.async_get_unit(unit_id)
        reading = await api.async_get_reading(unit_id)
        consumption = await api.async_get_consumption(unit_id, "day")
        values = [v for v in (consumption.get("values") or []) if v is not None]
        devices = detail.get("devices") or []
        device_id = devices[0].get("id") if devices else None
        try:
            incidents = await api.async_get_incidents(unit_id, device_id)
        except SuruApiError:
            incidents = None
        data[unit_id] = {
            "detail": detail,
            "reading": reading,
            "consumption_today_l": float(sum(values)) if values else None,
            "consumption": consumption,
            "incidents": incidents,
        }
    return data


def _local_midnight(dt: datetime) -> datetime:
    return dt.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def _api_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT00:00:00Z")


async def _daily_totals(api: SuruWaterApi, unit_id: str, days: int) -> list[tuple[datetime, int | None]]:
    """Per-day liters for the last `days` days, from chunked range=month calls.

    The API anchors value[i] to startDate + i days, so one range=month call
    covers a 31-day window ending at `today`.
    """
    today = _local_midnight(datetime.now())
    start = today - timedelta(days=days - 1)
    result: list[tuple[datetime, int | None]] = []
    cursor = start
    while cursor <= today:
        chunk_end = min(cursor + timedelta(days=30), today)
        data = await api._get(f"units/{unit_id}/consumption",
                              {"range": "month", "startDate": _api_date(cursor)})
        values = data.get("values") or []
        for i in range((chunk_end - cursor).days + 1):
            value = values[i] if i < len(values) else None
            result.append((cursor + timedelta(days=i), value))
        cursor = chunk_end + timedelta(days=1)
    return result


async def _hourly_buckets(api: SuruWaterApi, unit_id: str, day: str) -> list[tuple[int, int | None]]:
    day0 = datetime.strptime(day, "%Y-%m-%d")
    data = await api._get(f"units/{unit_id}/consumption",
                          {"range": "day", "startDate": _api_date(day0)})
    values = data.get("values") or []
    return [(hour, v) for hour, v in enumerate(values)]


def _print_daily_totals(unit_id: str, rows: list[tuple[datetime, int | None]]) -> None:
    print(f"\nunit {unit_id}: consumption per day (L)")
    print(f"  {'date':<12}{'liters':>9}")
    for day, value in rows:
        label = f"{value:>9}" if value is not None else "      -"
        print(f"  {day.strftime('%Y-%m-%d')}  {label}")


def _print_hourly(unit_id: str, day: str, rows: list[tuple[int, int | None]]) -> None:
    total = sum(v for _, v in rows if v is not None)
    print(f"\nunit {unit_id}: hourly consumption on {day} (L, total {total})")
    for hour, value in rows:
        label = f"{value:>9}" if value is not None else "      -"
        print(f"  {day} {hour:02d}:00  {label}")


async def _history_days(api: SuruWaterApi, days: int) -> None:
    units = await api.async_get_units()
    if not units:
        raise SystemExit("No water units on this account.")
    for unit in units:
        unit_id = unit.get("id")
        if not unit_id:
            continue
        _print_daily_totals(unit_id, await _daily_totals(api, unit_id, days))


async def _history_day(api: SuruWaterApi, day: str) -> None:
    units = await api.async_get_units()
    if not units:
        raise SystemExit("No water units on this account.")
    for unit in units:
        unit_id = unit.get("id")
        if not unit_id:
            continue
        _print_hourly(unit_id, day, await _hourly_buckets(api, unit_id, day))


async def _run_history(api: SuruWaterApi, args: argparse.Namespace) -> None:
    kind, _, value = args.history.partition("=")
    if kind == "days":
        try:
            days = int(value)
        except ValueError:
            raise SystemExit("--history days=N needs an integer N")
        await _history_days(api, max(1, days))
    elif kind == "day":
        await _history_day(api, value)
    else:
        raise SystemExit("--history must be 'days=N' or 'day=YYYY-MM-DD'")


async def _run(args: argparse.Namespace) -> None:
    email = args.email or os.environ.get("SURU_EMAIL")
    password = args.password or os.environ.get("SURU_PASSWORD")
    if not email or not password:
        raise SystemExit("Provide credentials via --email/--password or SURU_EMAIL/SURU_PASSWORD env vars.")

    async with aiohttp.ClientSession() as session:
        api = SuruWaterApi(session, email, password)
        try:
            await api.async_login()
        except SuruAuthError as err:
            raise SystemExit(f"Login failed: {err}")

        if args.history:
            await _run_history(api, args)
            return

        while True:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"== SURU WaterMonitor poll @ {stamp} ==")
            try:
                data = await _poll_once(api)
            except (SuruAuthError, SuruApiError) as err:
                print(f"  poll failed: {err}")
            else:
                if args.raw:
                    print(json.dumps(data, indent=2, default=str))
                else:
                    _print_report(data)

            if not args.interval:
                return
            print(f"-- next poll in {args.interval}s (Ctrl+C to stop) --")
            await asyncio.sleep(args.interval)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll SURU WaterMonitor locally.")
    parser.add_argument("--email", help="SURU account email (default: SURU_EMAIL)")
    parser.add_argument("--password", help="SURU account password (default: SURU_PASSWORD)")
    parser.add_argument("--interval", type=int, default=0,
                        help="poll every N seconds; 0 = poll once (default)")
    parser.add_argument("--raw", action="store_true",
                        help="print raw JSON instead of the sensor report")
    parser.add_argument("--history",
                        help="history report: 'days=30' for per-day totals, "
                             "'day=YYYY-MM-DD' for hourly buckets")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_run(_parse_args()))
