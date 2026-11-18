#!/usr/bin/env python3
"""Snapshot GPU availability on Vast.ai and RunPod and append it to CSV files.

Run every 20 minutes by .github/workflows/collect.yml. Standard library only.

Env:
  RUNPOD_API_KEY  required for RunPod (read-only key is enough)
  VAST_API_KEY    optional; Vast offer search works without it
"""
import csv
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())

VAST_URL = "https://console.vast.ai/api/v0/bundles/"
RUNPOD_URL = "https://api.runpod.io/graphql"
UA = {"User-Agent": "gpu-availability-tracker/1.0", "Accept": "application/json"}

VAST_FIELDS = ["ts", "gpu", "offers", "gpus", "single_gpu_offers", "dc_gpus", "eu_gpus",
               "fast_net_gpus", "reliable_gpus", "min_price", "median_price", "eu_min_price"]
RUNPOD_FIELDS = ["ts", "gpu_id", "name", "vram_gb", "secure_stock", "community_stock",
                 "secure_price", "community_price"]
VAST_REGION_FIELDS = ["ts", "gpu", "region", "gpus", "dc_gpus", "min_price"]
RUNPOD_DC_FIELDS = ["ts", "dc", "location", "gpu", "stock"]

COUNTRY_REGION = {cc: reg for reg, ccs in CONFIG.get("regions", {}).items() for cc in ccs}


def region_of(cc):
    return COUNTRY_REGION.get(cc, "Other")


def shard(prefix, ts):
    """Regional data is bulkier, so it goes in monthly files: data/regions/<prefix>-YYYY-MM.csv"""
    d = DATA / "regions"
    d.mkdir(exist_ok=True)
    return d / f"{prefix}-{ts[:7]}.csv"


def http_json(url, body=None, headers=None, timeout=40):
    h = dict(UA)
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def append_rows(path, fields, rows):
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def r4(x):
    return None if x is None else round(x, 4)


# ---------------- Vast.ai ----------------

def vast_search(query):
    headers = {}
    key = os.environ.get("VAST_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # GET with ?q= is what the vastai CLI uses; fall back to POST body.
    try:
        url = VAST_URL + "?q=" + urllib.parse.quote(json.dumps(query))
        res = http_json(url, headers=headers)
    except Exception:
        res = http_json(VAST_URL, body=query, headers=headers)
    return res.get("offers", []) if isinstance(res, dict) else []


def vast_country(o):
    geo = (o.get("geolocation") or "").strip()
    return geo.rsplit(",", 1)[-1].strip().upper() if "," in geo else geo.upper()


def vast_is_dc(o):
    return bool(o.get("hosting_type") == 1 or o.get("datacenter") or o.get("is_datacenter"))


def collect_vast(ts):
    eu = set(CONFIG["eu_countries"])
    rows, region_rows = [], []
    for name in CONFIG["vast_gpus"]:
        q = {
            "gpu_name": {"eq": name},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "type": "on-demand",
            "limit": 3000,
        }
        offers = vast_search(q)
        per_gpu, eu_prices = [], []
        by_region = {}
        gpus = single = dc = eu_g = fast = rel = 0
        for o in offers:
            n = int(o.get("num_gpus") or 1)
            price = o.get("dph_total")
            gpus += n
            if n == 1:
                single += 1
            if vast_is_dc(o):
                dc += n
            cc = vast_country(o)
            is_eu = cc in eu
            reg = by_region.setdefault(region_of(cc), {"gpus": 0, "dc": 0, "prices": []})
            reg["gpus"] += n
            if vast_is_dc(o):
                reg["dc"] += n
            if price:
                reg["prices"].append(price / n)
            if is_eu:
                eu_g += n
            if (o.get("inet_down") or 0) >= CONFIG["fast_net_mbps"]:
                fast += n
            if (o.get("reliability2") or o.get("reliability") or 0) >= CONFIG["min_reliability"]:
                rel += n
            if price:
                per_gpu.append(price / n)
                if is_eu:
                    eu_prices.append(price / n)
        rows.append({
            "ts": ts, "gpu": name, "offers": len(offers), "gpus": gpus,
            "single_gpu_offers": single, "dc_gpus": dc, "eu_gpus": eu_g,
            "fast_net_gpus": fast, "reliable_gpus": rel,
            "min_price": r4(min(per_gpu)) if per_gpu else None,
            "median_price": r4(statistics.median(per_gpu)) if per_gpu else None,
            "eu_min_price": r4(min(eu_prices)) if eu_prices else None,
        })
        for reg, a in sorted(by_region.items()):
            region_rows.append({"ts": ts, "gpu": name, "region": reg, "gpus": a["gpus"], "dc_gpus": a["dc"],
                                "min_price": r4(min(a["prices"])) if a["prices"] else None})
        time.sleep(1)  # be polite
    append_rows(DATA / "vast.csv", VAST_FIELDS, rows)
    append_rows(shard("vast", ts), VAST_REGION_FIELDS, region_rows)

    # Name discovery: what GPU names exist right now (helps fix config.json).
    try:
        allo = vast_search({"rentable": {"eq": True}, "rented": {"eq": False},
                            "type": "on-demand", "limit": 5000})
        names = {}
        for o in allo:
            nm = o.get("gpu_name")
            if nm:
                names[nm] = names.get(nm, 0) + int(o.get("num_gpus") or 1)
        (DATA / "vast_gpu_names.json").write_text(json.dumps(
            {"ts": ts, "sampled_offers": len(allo),
             "gpus_by_name": dict(sorted(names.items(), key=lambda kv: -kv[1]))}, indent=1))
    except Exception as e:
        print(f"vast name discovery failed: {e}", file=sys.stderr)
    return len(rows)


# ---------------- RunPod ----------------

RUNPOD_QUERY = """
query {
  gpuTypes {
    id
    displayName
    memoryInGb
    secure: lowestPrice(input: {gpuCount: 1, secureCloud: true}) { stockStatus uninterruptablePrice }
    community: lowestPrice(input: {gpuCount: 1, secureCloud: false}) { stockStatus uninterruptablePrice }
  }
}
"""

RUNPOD_DC_QUERY = """
query { dataCenters { id name location gpuAvailability { gpuId displayName stockStatus } } }
"""


def runpod_wanted(label):
    wanted = [w.lower() for w in CONFIG["runpod_gpus"]]
    return not wanted or any(w in label.lower() for w in wanted)


def collect_runpod(ts):
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("RUNPOD_API_KEY not set")
    res = http_json(RUNPOD_URL, body={"query": RUNPOD_QUERY},
                    headers={"Authorization": f"Bearer {key}"})
    if res.get("errors"):
        raise RuntimeError(json.dumps(res["errors"])[:500])
    rows = []
    for g in res.get("data", {}).get("gpuTypes", []):
        if not runpod_wanted(f"{g.get('id', '')} {g.get('displayName', '')}"):
            continue
        s, c = g.get("secure") or {}, g.get("community") or {}
        rows.append({
            "ts": ts, "gpu_id": g.get("id"), "name": g.get("displayName"),
            "vram_gb": g.get("memoryInGb"),
            "secure_stock": s.get("stockStatus") or "None",
            "community_stock": c.get("stockStatus") or "None",
            "secure_price": s.get("uninterruptablePrice"),
            "community_price": c.get("uninterruptablePrice"),
        })
    append_rows(DATA / "runpod.csv", RUNPOD_FIELDS, rows)

    # Per-datacenter stock. Serverless endpoints with a network volume are pinned to one DC,
    # so this is the view that matters for them. Only rows with stock are stored (absent = none).
    try:
        dres = http_json(RUNPOD_URL, body={"query": RUNPOD_DC_QUERY},
                         headers={"Authorization": f"Bearer {key}"})
        if dres.get("errors"):
            raise RuntimeError(json.dumps(dres["errors"])[:300])
        dc_rows = []
        for dc in dres.get("data", {}).get("dataCenters", []) or []:
            for ga in dc.get("gpuAvailability") or []:
                stock = ga.get("stockStatus")
                if not stock or stock == "None":
                    continue
                if not runpod_wanted(f"{ga.get('gpuId', '')} {ga.get('displayName', '')}"):
                    continue
                dc_rows.append({"ts": ts, "dc": dc.get("id"), "location": dc.get("location") or dc.get("name"),
                                "gpu": ga.get("displayName") or ga.get("gpuId"), "stock": stock})
        append_rows(shard("runpod", ts), RUNPOD_DC_FIELDS, dc_rows)
    except Exception as e:
        print(f"runpod datacenter query failed: {e}", file=sys.stderr)
    return len(rows)


def main():
    DATA.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    status = {"ts": ts}
    for name, fn in (("vast", collect_vast), ("runpod", collect_runpod)):
        try:
            status[name] = {"ok": True, "rows": fn(ts)}
        except Exception as e:
            status[name] = {"ok": False, "error": str(e)[:500]}
            print(f"{name} failed: {e}", file=sys.stderr)
    (DATA / "status.json").write_text(json.dumps(status, indent=1))
    print(json.dumps(status))
    # Fail the job only if both providers failed, so one outage doesn't hide the other.
    if not any(v.get("ok") for k, v in status.items() if k != "ts"):
        sys.exit(1)


if __name__ == "__main__":
    main()
