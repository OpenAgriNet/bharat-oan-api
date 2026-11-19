"""Repoint a registered provider action at a different mapping file.

Why this exists: quick-start/bin/setup.py registers create-only. Its
ensure_binding() returns early the moment the binding key is already present,
so `make register-provider` can add a ProviderSchema but can never correct one.
Change a mapping URL in .env, re-run register-provider, and it prints
"already present" and leaves the old URL live -- which is a quiet way to spend
an afternoon debugging a fix that was never actually loaded.

The registry is only reachable from inside the Docker network (port 8081 is
not published on the host), so run this in a container on oan-internal:

    docker exec -e PYTHONPATH=/app sva_app python scripts/registry_set_mapping.py \
      --binding 'mausamgram|openagrinet:WeatherObservation' \
      --mapping https://raw.githubusercontent.com/.../weather-observation.select.yaml

Pass --action to target something other than select. Prints the old and new
URL and exits non-zero if the binding does not exist, so a typo in the binding
key fails loudly rather than silently updating nothing.

The adapter caches fetched mappings for about a minute (cacheTTL), so give it
that long before expecting the next select to use the new file.
"""

import argparse
import sys

import httpx

REGISTRY = "http://sunbird-registry-service:8081/api/v1/ProviderSchema"


def _strip_os_fields(record: dict) -> dict:
    """Drop the registry's own bookkeeping keys.

    osid, osCreatedAt, osUpdatedBy and friends are assigned by the registry.
    Sending them back in an update body is at best ignored and at worst
    rejected, so the record is round-tripped without them.
    """
    return {k: v for k, v in record.items() if not k.startswith("os")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binding", required=True, help="participantId|capabilityCode")
    ap.add_argument("--mapping", required=True, help="new mapping URL")
    ap.add_argument("--action", default="select")
    args = ap.parse_args()

    found = httpx.post(
        f"{REGISTRY}/search",
        json={"filters": {"bindingKey": {"eq": args.binding}}},
        timeout=30,
    )
    found.raise_for_status()
    rows = found.json().get("data", [])
    if not rows:
        print(f"no ProviderSchema with bindingKey {args.binding!r}")
        return 1

    record = rows[0]
    osid = record["osid"]

    body = _strip_os_fields(record)
    body["actions"] = []
    changed = False
    for action in record.get("actions", []):
        action = _strip_os_fields(action)
        if action.get("action") == args.action:
            print(f"  {args.action}: was {action.get('mappings')}")
            action["mappings"] = args.mapping
            changed = True
        body["actions"].append(action)

    if not changed:
        print(f"binding {args.binding} has no {args.action!r} action")
        return 1

    put = httpx.put(
        f"{REGISTRY}/{osid}",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if put.status_code >= 300:
        print(f"update failed: {put.status_code} {put.text[:300]}")
        return 1

    print(f"  {args.action}: now {args.mapping}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
