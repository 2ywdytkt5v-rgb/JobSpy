"""Collect public job-board metadata on a GitHub runner, not the chat container.

This only creates raw JobSpy checkpoints. Screening, official-JD verification,
Excel updating and actual Gmail delivery remain separate required stages.
"""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys
import time

OUT = Path("jobspy-output")
FAMILIES = [
    ("credit-coverage", "credit analyst OR corporate banking OR client coverage"),
    ("rating-dcm", "rating analyst OR debt capital markets"),
    ("fixed-income", "fixed income OR credit research OR FICC"),
    ("treasury", "treasury analyst OR cash management"),
    ("finance-fpa", "financial analyst OR FP&A OR budgeting"),
    ("business-analytics", "business analyst OR business planning"),
    ("valuation-advisory", "valuation analyst OR transaction advisory"),
    ("research-investment", "research analyst OR investment analyst"),
    ("product-client", "product analyst OR client solutions OR sales support"),
    ("reporting-operations", "reporting analyst OR operations analyst"),
]
MARKETS = [
    ("Shanghai", "Shanghai, China", "China"),
    ("Hong Kong", "Hong Kong", "hong kong"),
]
QUERIES = [
    {"site": site, "city": city, "location": location, "country": country,
     "family": family, "search_term": term}
    for site in ("linkedin", "indeed")
    for city, location, country in MARKETS
    for family, term in FAMILIES
]


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def worker(index):
    from jobspy import scrape_jobs
    q = QUERIES[index]
    args = dict(site_name=q["site"], search_term=q["search_term"],
                location=q["location"], results_wanted=45, hours_old=720)
    if q["site"] == "linkedin":
        args["linkedin_fetch_description"] = False
    else:
        args["country_indeed"] = q["country"]
    data = scrape_jobs(**args).to_dict(orient="records")
    OUT.mkdir(exist_ok=True)
    (OUT / f"_query_{index:03d}.json").write_text(dump(data), encoding="utf-8")
    print(dump({"query": index, "rows": len(data)}))


def collect(smoke=False):
    OUT.mkdir(exist_ok=True)
    began = time.monotonic()
    # Pull requests run two smoke queries; scheduled runs use all 40.
    selected = [0, 30] if smoke else list(range(len(QUERIES)))
    rows, audits = [], []
    for index in selected:
        q = QUERIES[index]
        began_query = time.monotonic()
        try:
            p = subprocess.run([sys.executable, __file__, "--worker", str(index)],
                               capture_output=True, text=True, timeout=105)
            if p.returncode:
                raise RuntimeError((p.stderr or p.stdout)[-1800:])
            found = json.loads((OUT / f"_query_{index:03d}.json").read_text(encoding="utf-8"))
            for record in found:
                record["_source_site"] = q["site"]
                record["_query_family"] = q["family"]
                record["_query_location"] = q["location"]
                record["_checked_utc"] = datetime.now(timezone.utc).isoformat()
            rows.extend(found)
            status, error = "ok", ""
        except subprocess.TimeoutExpired:
            found, status, error = [], "timeout", "Query exceeded 105 seconds"
        except Exception as exc:
            found, status, error = [], "error", str(exc)[:1800]
        audits.append({**q, "status": status, "rows": len(found),
                       "elapsed_seconds": round(time.monotonic() - began_query, 1),
                       "error": error})
        print(f"[{index + 1}/{len(QUERIES)}] {q['site']} {q['city']} "
              f"{q['family']}: {status} {len(found)}", flush=True)

    cols = ["site", "title", "company", "city", "state", "country", "date_posted",
            "job_url", "job_url_direct", "description", "job_type", "min_amount",
            "max_amount", "currency", "_source_site", "_query_family",
            "_query_location", "_checked_utc"]
    with (OUT / "jobs.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with gzip.open(OUT / "raw_jobs.jsonl.gz", "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(dump(row) + "\n")
    with (OUT / "query_audit.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(audits[0].keys()))
        writer.writeheader()
        writer.writerows(audits)
    summary = {
        "complete": len(selected) == len(QUERIES) and all(x["status"] == "ok" for x in audits),
        "smoke_test_only": smoke,
        "queries_requested": len(selected),
        "queries_succeeded": sum(x["status"] == "ok" for x in audits),
        "raw_rows": len(rows),
        "unique_urls": len(set(str(x.get("job_url") or "") for x in rows if x.get("job_url"))),
        "elapsed_seconds": round(time.monotonic() - began, 1),
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Raw collection only; no official verification, historical dedupe, Excel or Gmail."
    }
    (OUT / "summary.json").write_text(dump(summary), encoding="utf-8")
    print(dump(summary), flush=True)
    return 0 if summary["queries_succeeded"] > 0 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--worker", type=int)
    p.add_argument("--smoke", action="store_true")
    parsed = p.parse_args()
    if parsed.worker is not None:
        worker(parsed.worker)
    else:
        sys.exit(collect(smoke=parsed.smoke))
