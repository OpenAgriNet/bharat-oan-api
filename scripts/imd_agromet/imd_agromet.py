"""IMD agromet advisory loader for the imd_agromet database.

    python imd_agromet.py setup               # tables, imd_lookup() and IMD -> LGD mappings
    python imd_agromet.py seed-keys           # resolver keys per LGD district
    python imd_agromet.py fetch [--date D]    # one day's bulletin (default: today, IST)
    python imd_agromet.py status

Env: IMD_DB_URL. Cron: */30 * * * 2,5 (IMD publishes on Tue and Fri).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import re
import sys
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import Json, execute_values

HERE = Path(__file__).resolve().parent
MASTERS = HERE / "masters"
IMD_URL = os.environ.get("IMD_URL", "https://agromet.imd.gov.in/index.php/api/moag/district_rv_adv/{date}/3")
FIELDS = ["id", "cat_id", "state_id", "district_id", "custom_date", "type_id", "variety_id",
          "advisory_eng", "advisory_reg", "crop_stage", "crop_state"]


def connect():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    url = os.environ.get("IMD_DB_URL")
    if not url:
        sys.exit("IMD_DB_URL is not set (postgresql://user:pass@host:5432/imd_agromet).")
    return psycopg2.connect(url)


def _int(v):
    v = str(v if v is not None else "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def _csv(name: str) -> list[dict]:
    return list(csv.DictReader((MASTERS / name).open(encoding="utf-8-sig")))


# Language names used in masters/imd/language_tb.csv -> language codes used by the chat
LANGUAGE_CODES = {"assamese": "as", "bengali": "bn", "english": "en", "gujarati": "gu", "hindi": "hi",
                  "kannada": "kn", "malayalam": "ml", "malyalam": "ml", "marathi": "mr", "mizo": "lus",
                  "nepali": "ne", "odia": "or", "punjabi": "pa", "tamil": "ta", "telugu": "te"}


def _crop_rows() -> list[tuple]:
    """One row per IMD type_id from the official crop table: display name, lower-case aliases.

    "PIGEON PEA (RED GRAM/ARHAR)" -> ("Pigeon pea", ["red gram", "arhar"]). The table repeats each id once
    per language; the upper-case English entries are used when present.
    """
    variants: dict[int, list[str]] = {}
    for r in _csv("imd/crop_tb.csv"):
        name = " ".join(r["crop_name"].split())
        if name and _int(r["crop_id"]) is not None:
            variants.setdefault(_int(r["crop_id"]), []).append(name)
    rows = []
    for type_id, names in variants.items():
        parts: list[str] = []
        for name in [n for n in names if n == n.upper()] or names:
            for part in re.split(r"[()/]", name):
                part = part.strip().lower()
                if part and part not in parts:
                    parts.append(part)
        rows.append((type_id, parts[0].capitalize(), parts[1:], "imd_master"))
    return rows


# ------------------------------------------------------------------ setup
def setup(conn) -> dict:
    with conn, conn.cursor() as cur:
        cur.execute((HERE / "schema.sql").read_text(encoding="utf-8"))
        execute_values(cur, "INSERT INTO imd_state_map VALUES %s ON CONFLICT (imd_state_id) DO UPDATE SET "
                            "state_name=EXCLUDED.state_name, state_reg_name=EXCLUDED.state_reg_name, latitude=EXCLUDED.latitude, "
                            "longitude=EXCLUDED.longitude, lgd_state_code=EXCLUDED.lgd_state_code",
                       [(_int(r["state_id"]), r["state_name"], r["state_reg_name"], float(r["latitude"] or 0) or None,
                         float(r["longitude"] or 0) or None, _int(r["state_lg_code"])) for r in _csv("state_tb.csv")])
        cur.execute("DELETE FROM imd_district_map")
        execute_values(cur, "INSERT INTO imd_district_map VALUES %s",
                       [(_int(r["imd_district_id"]), _int(r["imd_state_id"]), r["district_name"], _int(r["lgd_district_code"]),
                         _int(r["lgd_state_code"]), r["source"]) for r in _csv("district_map.csv")])
        crops = _crop_rows()
        cur.execute("DELETE FROM imd_crop_map")
        execute_values(cur, "INSERT INTO imd_crop_map (type_id, crop, aliases, source) VALUES %s", crops)
        cur.execute("DELETE FROM imd_category_map")
        execute_values(cur, "INSERT INTO imd_category_map VALUES %s",
                       [(_int(r["cat_id"]), r["category_name"].strip()) for r in _csv("imd/category_tb.csv")
                        if r["lang_id"].strip() == "2"])
        cur.execute("DELETE FROM imd_crop_stage_map")
        execute_values(cur, "INSERT INTO imd_crop_stage_map VALUES %s",
                       [(_int(r["Growth_Stage_Id"]), r["name"].strip()) for r in _csv("imd/growth_stage_tb.csv")
                        if r["name"].strip().upper() != "NULL"])
        cur.execute("DELETE FROM imd_state_language")
        execute_values(cur, "INSERT INTO imd_state_language VALUES %s",
                       [(_int(r["lang_state_id"]), r["reg_lang_name"].strip(),
                         LANGUAGE_CODES.get(r["reg_lang_name"].strip().lower())) for r in _csv("imd/language_tb.csv")])
        # rows already stored follow the (possibly corrected) district map
        cur.execute("UPDATE advisories a SET lgd_district_code = m.lgd_district_code FROM imd_district_map m "
                    "WHERE m.imd_district_id::text = a.district_id AND a.lgd_district_code IS DISTINCT FROM m.lgd_district_code")
        remapped = cur.rowcount
        cur.execute("UPDATE advisories SET valid_until = imd_valid_until(custom_date::date) WHERE valid_until IS NULL")
        cur.execute("SELECT source, count(*) FROM imd_district_map GROUP BY 1 ORDER BY 1")
        districts = dict(cur.fetchall())
    return {"district_map": districts, "crops": len(crops), "advisories_remapped": remapped}


def seed_keys(conn) -> dict:
    with gzip.open(MASTERS / "district_keys.csv.gz", "rt", encoding="utf-8") as f:
        rows = [(int(r["lgd_district_code"]), r["district_name"], r["key"], r["key_type"]) for r in csv.DictReader(f)]
    with conn, conn.cursor() as cur:
        cur.execute("TRUNCATE district_keys")
        execute_values(cur, "INSERT INTO district_keys VALUES %s", rows, page_size=10000)
        cur.execute("SELECT count(DISTINCT lgd_district_code) FROM district_keys")
        n = cur.fetchone()[0]
    return {"keys": len(rows), "districts": n}


# ------------------------------------------------------------------ fetch (cron)
def _download(date: str) -> tuple[list, str]:
    url = IMD_URL.format(date=date)
    r = httpx.get(url, timeout=120)
    r.raise_for_status()
    data = r.json()
    return (data if isinstance(data, list) else []), url


def fetch(conn, date: str) -> dict:
    rows, src = _download(date)
    rows = [r for r in rows if isinstance(r, dict) and r.get("id") is not None]
    with conn, conn.cursor() as cur:
        cur.execute("SELECT imd_district_id, lgd_district_code FROM imd_district_map")
        dmap = dict(cur.fetchall())
        ids = [str(r["id"]) for r in rows]
        cur.execute("SELECT id, raw_json FROM advisories WHERE id = ANY(%s)", (ids,))
        old = dict(cur.fetchall())
        new, changed, write = 0, 0, []
        for r in rows:
            rid = str(r["id"])
            if rid in old and old[rid] == r:
                continue
            new += rid not in old
            changed += rid in old
            vals = [rid] + [None if r.get(f) is None else str(r.get(f)) for f in FIELDS[1:]]
            write.append((*vals, Json(r), dmap.get(_int(r.get("district_id")))))
        if write:
            execute_values(cur, f"INSERT INTO advisories ({', '.join(FIELDS)}, raw_json, lgd_district_code) VALUES %s "
                                "ON CONFLICT (id) DO UPDATE SET "
                                + ", ".join(f"{f}=EXCLUDED.{f}" for f in FIELDS[1:])
                                + ", raw_json=EXCLUDED.raw_json, lgd_district_code=EXCLUDED.lgd_district_code, updated_at=now()",
                           write, page_size=1000)
            cur.execute("UPDATE advisories SET valid_until = imd_valid_until(custom_date::date) WHERE id = ANY(%s)",
                        ([w[0] for w in write],))
        # IMD district IDs not in the map yet: note them so they show up in status
        unknown = {(_int(r.get("district_id")), _int(r.get("state_id"))) for r in rows} - {(None, None)}
        unknown = [(d, s) for d, s in unknown if d is not None and d not in dmap]
        if unknown:
            execute_values(cur, "INSERT INTO imd_district_map (imd_district_id, imd_state_id, source) VALUES %s "
                                "ON CONFLICT (imd_district_id) DO NOTHING", [(d, s, "unmatched") for d, s in unknown])
        bulletins = sorted({str(r.get("custom_date")) for r in rows if r.get("custom_date")})
        cur.execute("INSERT INTO fetches (bulletin, source, rows, new_rows, changed_rows) VALUES (%s,%s,%s,%s,%s)",
                    (",".join(bulletins) or date, src, len(rows), new, changed))
        cur.execute("SELECT count(*) FILTER (WHERE a.lgd_district_code IS NULL), "
                    "count(DISTINCT a.lgd_district_code) FILTER (WHERE a.lgd_district_code IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM district_keys k WHERE k.lgd_district_code = a.lgd_district_code)) "
                    "FROM advisories a WHERE a.id = ANY(%s)", (ids,))
        unmapped_rows, no_keys = cur.fetchone()
    return {"bulletin": bulletins, "source": src, "rows": len(rows), "new": new, "changed": changed,
            "unmapped_rows": unmapped_rows, "new_imd_districts": len(unknown), "districts_without_keys": no_keys}


def status(conn) -> dict:
    with conn, conn.cursor() as cur:
        q = lambda sql: (cur.execute(sql), cur.fetchall())[1]
        return {
            "bulletins": {d: n for d, n in q("SELECT custom_date, count(*) FROM advisories GROUP BY 1 ORDER BY 1 DESC LIMIT 5")},
            "advisories_unmapped": q("SELECT count(*) FROM advisories WHERE lgd_district_code IS NULL")[0][0],
            "district_keys": q("SELECT count(*), count(DISTINCT lgd_district_code) FROM district_keys")[0],
            "district_map": dict(q("SELECT source, count(*) FROM imd_district_map GROUP BY 1 ORDER BY 1")),
            "last_fetch": [str(v) for v in (q("SELECT bulletin, rows, new_rows, changed_rows, fetched_at FROM fetches "
                                              "ORDER BY id DESC LIMIT 1") or [[]])[0]],
        }


def _bulletin_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from None


def main() -> None:
    ist = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("seed-keys")
    f = sub.add_parser("fetch")
    f.add_argument("--date", default=ist, type=_bulletin_date)
    sub.add_parser("status")
    a = ap.parse_args()
    conn = connect()
    try:
        if a.cmd == "setup":
            out = setup(conn)
        elif a.cmd == "seed-keys":
            out = seed_keys(conn)
        elif a.cmd == "fetch":
            out = fetch(conn, a.date)
        else:
            out = status(conn)
    except httpx.HTTPError as exc:
        sys.exit(f"{dt.datetime.now():%Y-%m-%d %H:%M} IMD download failed: {exc}")
    finally:
        conn.close()
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M}", a.cmd, json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
