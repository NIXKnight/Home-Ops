#!/usr/bin/env python3
"""Render/check Hybrid Solar Plant profiles. Local files/stdout only; no Grafana API.

The canonical JSON is cluster-shaped. Gateway identities must be supplied by the
Operator from the existing dashboard/datasource; there are deliberately no guesses.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ansible/files/monitoring/hybrid-solar-plant.json"
JSON_MARKER = "  json: |\n"


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get("panels", []))


def load_source():
    return json.loads(SOURCE.read_text())


def render(profile="cluster", dashboard_uid=None, datasource_uid=None):
    if profile not in {"cluster", "gateway"}:
        raise ValueError("profile must be cluster or gateway")
    if profile == "gateway":
        if not dashboard_uid or not datasource_uid:
            raise ValueError("gateway requires existing --dashboard-uid AND --datasource-uid")
        for uid in (dashboard_uid, datasource_uid):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", uid):
                raise ValueError("UID must contain 1-40 letters, digits, underscores or hyphens")
    elif dashboard_uid is not None or datasource_uid is not None:
        raise ValueError("cluster identities are fixed; UID overrides are gateway-only")
    dashboard = copy.deepcopy(load_source())
    dashboard.pop("id", None)
    dashboard["uid"] = "hybrid-solar-plant" if profile == "cluster" else dashboard_uid
    uid = "prometheus" if profile == "cluster" else datasource_uid

    def rewrite(value):
        if isinstance(value, dict):
            if value.get("type") == "prometheus" and "uid" in value:
                value["uid"] = uid
            for child in value.values():
                rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)

    rewrite(dashboard)
    cadence = "5" if profile == "cluster" else "10"
    variable = next(v for v in dashboard["templating"]["list"] if v["name"] == "solar_sample_interval_s")
    variable.update(query=cadence, current={"text": cadence, "value": cadence},
                    options=[{"text": cadence, "value": cadence, "selected": True}])
    return dashboard


def manifest_dashboard(path):
    text = Path(path).read_text()
    if text.count(JSON_MARKER) != 1:
        raise ValueError("expected one terminal spec.json block")
    _, block = text.split(JSON_MARKER)
    return json.loads(textwrap.dedent(block))


def manifest_text(path, dashboard):
    text = Path(path).read_text()
    # Validate the existing terminal block before replacing it; never discard
    # trailing YAML resources or unknown configuration silently.
    manifest_dashboard(path)
    prefix = text.split(JSON_MARKER)[0]
    return prefix + JSON_MARKER + textwrap.indent(json.dumps(dashboard, indent=2, ensure_ascii=True) + "\n", "    ")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["cluster", "gateway"], required=True)
    parser.add_argument("--dashboard-uid", help="confirmed existing gateway dashboard UID")
    parser.add_argument("--datasource-uid", help="confirmed existing gateway Prometheus datasource UID")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check-manifest", type=Path, help="compare cluster CR JSON without writing")
    actions.add_argument("--write-manifest", type=Path, help="update only an explicitly named local cluster CR JSON block")
    args = parser.parse_args(argv)
    try:
        dashboard = render(args.profile, args.dashboard_uid, args.datasource_uid)
        if args.check_manifest or args.write_manifest:
            if args.profile != "cluster":
                raise ValueError("manifest actions require the cluster profile")
            path = args.check_manifest or args.write_manifest
            if args.check_manifest:
                if manifest_dashboard(path) != dashboard:
                    raise ValueError("cluster manifest differs from canonical profile; regenerate it")
                print("Cluster manifest matches canonical dashboard")
            else:
                path.write_text(manifest_text(path, dashboard))
        else:
            print(json.dumps(dashboard, indent=2, ensure_ascii=True))
    except (ValueError, OSError, StopIteration) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
