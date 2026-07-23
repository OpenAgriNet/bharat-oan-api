#!/usr/bin/env python3
"""Audit NPSS master data for sub-districts with no village rows.

This script is read-only. It walks the live NPSS Vistaar master endpoints:
States -> Districts -> SubDistricts -> Vilages, and writes the empty village
hierarchies plus request errors to JSON and CSV files.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


def _rows(payload: Any) -> list[dict[str, Any]]:
    """Unwrap the list shape used by NPSS, tolerating common envelopes."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result", "results", "items", "records", "response"):
        if key not in payload:
            continue
        nested = _rows(payload[key])
        if nested:
            return nested
    return [payload] if any(str(key).lower().endswith("id") for key in payload) else []


def _value(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _id(row: dict[str, Any], *keys: str) -> str:
    value = _value(row, tuple(keys))
    if value:
        return value
    return _value(
        row,
        tuple(str(key) for key in row if str(key).lower().endswith("id")),
    )


def _name(row: dict[str, Any], *keys: str) -> str:
    value = _value(row, tuple(keys))
    if value:
        return value
    return _value(
        row,
        tuple(
            str(key)
            for key in row
            if "name" in str(key).lower() or str(key).lower() in {"title", "label"}
        ),
    )


class NpssMasterClient:
    def __init__(self, base_url: str, username: str, password: str, concurrency: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client: httpx.AsyncClient | None = None
        self.token = ""
        self.semaphore = asyncio.Semaphore(max(1, concurrency))

    async def __aenter__(self) -> "NpssMasterClient":
        self.client = httpx.AsyncClient(timeout=45.0)
        await self._authenticate()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def _authenticate(self) -> None:
        assert self.client is not None
        response = await self.client.post(
            f"{self.base_url}/api/Vistaar/token",
            json={"userName": self.username, "password": self.password},
            headers={"accept": "text/plain", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        self.token = str(payload.get("token") or payload.get("access_token") or "")
        if not self.token:
            raise RuntimeError("NPSS token response did not contain a token")

    async def get_rows(self, endpoint: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        assert self.client is not None
        async with self.semaphore:
            for attempt in range(3):
                response = await self.client.get(
                    f"{self.base_url}/api/Vistaar/{endpoint}",
                    params=params,
                    headers={"accept": "*/*", "Authorization": f"Bearer {self.token}"},
                )
                if response.status_code == 401 and attempt == 0:
                    await self._authenticate()
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
                        continue
                response.raise_for_status()
                return _rows(response.json())
        raise RuntimeError(f"NPSS request failed after retries: {endpoint} {params}")


async def audit(args: argparse.Namespace) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    empty_subdistricts: list[dict[str, Any]] = []
    districts_without_subdistricts: list[dict[str, Any]] = []
    states_without_districts: list[dict[str, Any]] = []

    async with NpssMasterClient(args.base_url, args.username, args.password, args.concurrency) as api:
        states = await api.get_rows("States")

        async def load_districts(state: dict[str, Any]):
            state_id = _id(state, "stateId", "state_id", "id")
            try:
                return state, await api.get_rows("Districts", {"stateId": state_id})
            except Exception as exc:
                errors.append({"level": "district", "state_id": state_id, "error": str(exc)})
                return state, []

        district_groups = await asyncio.gather(*(load_districts(state) for state in states))
        districts: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for state, district_rows in district_groups:
            if not district_rows:
                states_without_districts.append(
                    {
                        "state_id": _id(state, "stateId", "state_id", "id"),
                        "state_name": _name(state, "stateName", "state", "name"),
                    }
                )
            districts.extend((state, district) for district in district_rows)

        async def load_subdistricts(state: dict[str, Any], district: dict[str, Any]):
            state_id = _id(state, "stateId", "state_id", "id")
            district_id = _id(district, "districtId", "district_id", "id")
            try:
                return state, district, await api.get_rows(
                    "SubDistricts",
                    {"stateId": state_id, "districtId": district_id},
                )
            except Exception as exc:
                errors.append(
                    {
                        "level": "subdistrict",
                        "state_id": state_id,
                        "district_id": district_id,
                        "error": str(exc),
                    }
                )
                return state, district, []

        subdistrict_groups = await asyncio.gather(
            *(load_subdistricts(state, district) for state, district in districts)
        )
        subdistricts: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for state, district, subdistrict_rows in subdistrict_groups:
            if not subdistrict_rows:
                districts_without_subdistricts.append(
                    {
                        "state_id": _id(state, "stateId", "state_id", "id"),
                        "state_name": _name(state, "stateName", "state", "name"),
                        "district_id": _id(district, "districtId", "district_id", "id"),
                        "district_name": _name(district, "districtName", "district", "name"),
                    }
                )
            subdistricts.extend(
                (state, district, subdistrict)
                for subdistrict in subdistrict_rows
            )

        async def load_villages(
            state: dict[str, Any],
            district: dict[str, Any],
            subdistrict: dict[str, Any],
        ) -> None:
            state_id = _id(state, "stateId", "state_id", "id")
            district_id = _id(district, "districtId", "district_id", "id")
            subdistrict_id = _id(
                subdistrict,
                "subDistrictId",
                "subdistrictId",
                "sub_district_id",
                "id",
            )
            params = {
                "stateId": state_id,
                "districtId": district_id,
                "subDistrictId": subdistrict_id,
            }
            try:
                villages = await api.get_rows("Vilages", params)
            except Exception as exc:
                errors.append({"level": "village", **params, "error": str(exc)})
                return
            if not villages:
                empty_subdistricts.append(
                    {
                        "state_id": state_id,
                        "state_name": _name(state, "stateName", "state", "name"),
                        "district_id": district_id,
                        "district_name": _name(district, "districtName", "district", "name"),
                        "sub_district_id": subdistrict_id,
                        "sub_district_name": _name(
                            subdistrict,
                            "subDistrictName",
                            "subdistrictName",
                            "sub_district_name",
                            "name",
                        ),
                        "village_count": 0,
                    }
                )

        await asyncio.gather(*(load_villages(*item) for item in subdistricts))

    empty_subdistricts.sort(
        key=lambda row: (
            row["state_name"],
            row["district_name"],
            row["sub_district_name"],
        )
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": generated_at,
        "base_url": args.base_url.rstrip("/"),
        "summary": {
            "states": len(states),
            "districts": len(districts),
            "subdistricts": len(subdistricts),
            "empty_subdistricts": len(empty_subdistricts),
            "districts_without_subdistricts": len(districts_without_subdistricts),
            "states_without_districts": len(states_without_districts),
            "request_errors": len(errors),
        },
        "empty_subdistricts": empty_subdistricts,
        "districts_without_subdistricts": districts_without_subdistricts,
        "states_without_districts": states_without_districts,
        "request_errors": errors,
    }


def _write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "npss-empty-villages.json"
    csv_path = output_dir / "npss-empty-villages.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    rows = report["empty_subdistricts"]
    fields = [
        "state_id",
        "state_name",
        "district_id",
        "district_name",
        "sub_district_id",
        "sub_district_name",
        "village_count",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def _parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("NPSS_BASE_URL", "https://npss.dac.gov.in/api3.0"))
    parser.add_argument("--username", default=os.getenv("NPSS_USERNAME"))
    parser.add_argument("--password", default=os.getenv("NPSS_PASSWORD"))
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, default=repo_root / ".local-dev-logs")
    args = parser.parse_args()
    if not args.username or not args.password:
        parser.error("NPSS_USERNAME and NPSS_PASSWORD are required (set them in .env or pass flags)")
    return args


def main() -> None:
    args = _parse_args()
    report = asyncio.run(audit(args))
    json_path, csv_path = _write_reports(report, args.output_dir)
    print(json.dumps(report["summary"], indent=2))
    print(f"JSON report: {json_path}")
    print(f"CSV report:  {csv_path}")
    if report["request_errors"]:
        raise SystemExit("Audit completed with request errors; see the JSON report.")


if __name__ == "__main__":
    main()
