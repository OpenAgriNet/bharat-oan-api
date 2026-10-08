"""Sync a commodity_codes.json file with the latest Agmarknet commodity master (option=2).

Rules applied to each entry:
  - Removed:  code no longer in the master -> dropped; its terms move to the superseding code
              (SUPERSEDED_BY below). A removed code without a mapping aborts the run.
  - Renamed:  same code, name differs from master -> canonical name updated.
  - Term replaced: a term equal to the old name is swapped for the new name.
  - Term appended: the new name is added as a term when not already present.
  - Added:    code in master but missing from the file -> new entry with the name as its only term.

Names are compared with surrounding whitespace stripped (the master has stray
spaces/newlines, e.g. "Sweet Corn ").

Usage:
  # Fetch the master live (needs AGMARKNET_BASE_URL, AGMARKNET_ACCESS_NAME, AGMARKNET_PASSWORD)
  python scripts/sync_commodity_codes.py assets/commodity_codes.json
  # Or use a saved master response
  python scripts/sync_commodity_codes.py assets/commodity_codes.json --master comm.json
  # Report only, don't write
  python scripts/sync_commodity_codes.py assets/commodity_codes.json --dry-run
"""
import argparse
import json
import os
import sys

# Removed code -> existing code that supersedes it in the current master.
SUPERSEDED_BY = {
    7: 45,     # Red Gram -> Red gram/Arhar/Tur(whole)
    77: 223,   # Dal(Avare) -> Avare Dal
    123: 61,   # Jaggery -> Gur(Jaggery)
    238: 14,   # Sunflower Seed -> Sunflower/Sunflower Seed
}


def fetch_master() -> list[dict]:
    import httpx

    base = os.environ["AGMARKNET_BASE_URL"].rstrip("/")
    token = httpx.post(
        f"{base}/v1/generate-dynamic-token-agmarknet",
        json={
            "access_name": os.environ["AGMARKNET_ACCESS_NAME"],
            "password": os.environ["AGMARKNET_PASSWORD"],
        },
        timeout=30,
    ).json()["token"]
    resp = httpx.get(
        f"{base}/v1/fetch-agmarknet-master-data",
        params={"option": "2", "token": token},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _add_term(terms: list[str], term: str) -> bool:
    if any(t.strip().lower() == term.lower() for t in terms):
        return False
    terms.append(term)
    return True


def sync(entries: list[dict], master: list[dict]) -> tuple[list[dict], dict]:
    master_names = {
        int(m["commodity_id"]): str(m["commodity_name"]).strip()
        for m in master
        if m.get("commodity_id") is not None and m.get("commodity_name")
    }
    by_code = {e["code"]: {**e, "terms": list(e["terms"])} for e in entries}
    report = {"removed": [], "renamed": [], "terms_replaced": [], "terms_appended": [], "added": []}

    for code in [c for c in by_code if c not in master_names]:
        target = SUPERSEDED_BY.get(code)
        if target is None or target not in master_names:
            sys.exit(f"Code {code} ({by_code[code]['name']}) is gone from the master and has no "
                     f"superseding code in SUPERSEDED_BY — add one and re-run.")
        removed = by_code.pop(code)
        if target not in by_code:
            by_code[target] = {"code": target, "name": master_names[target], "terms": []}
        for term in removed["terms"]:
            _add_term(by_code[target]["terms"], term)
        report["removed"].append(f"{code} {removed['name']} -> {target} {master_names[target]}")

    for code, name in master_names.items():
        if code not in by_code:
            by_code[code] = {"code": code, "name": name, "terms": [name]}
            report["added"].append(f"{code} {name}")
            continue

        entry = by_code[code]
        old = entry["name"]
        if old.strip() != name:
            entry["name"] = name
            report["renamed"].append(f"{code} {old!r} -> {name!r}")
        elif old != name:
            entry["name"] = name  # whitespace-only difference; not worth reporting

        if old.strip() == name:
            continue
        for i, term in enumerate(entry["terms"]):
            if term.strip() == old.strip():
                entry["terms"][i] = name
                report["terms_replaced"].append(f"{code} {term!r} -> {name!r}")
                break
        else:
            if _add_term(entry["terms"], name):
                report["terms_appended"].append(f"{code} {name!r}")

    return sorted(by_code.values(), key=lambda e: e["code"]), report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("codes_file", help="Path to commodity_codes.json to update")
    parser.add_argument("--master", help="Saved master-data option=2 JSON (default: fetch live)")
    parser.add_argument("--dry-run", action="store_true", help="Print the report without writing")
    args = parser.parse_args()

    with open(args.codes_file, encoding="utf-8") as f:
        entries = json.load(f)
    if args.master:
        with open(args.master, encoding="utf-8") as f:
            master = json.load(f)
    else:
        master = fetch_master()

    updated, report = sync(entries, master)

    for key, lines in report.items():
        print(f"{key} ({len(lines)})")
        for line in lines:
            print(f"  {line}")
    print(f"entries: {len(entries)} -> {len(updated)}")

    if not args.dry_run:
        with open(args.codes_file, "w", encoding="utf-8") as f:
            json.dump(updated, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"wrote {args.codes_file}")


if __name__ == "__main__":
    main()
