“””
list_gke_projects.py
────────────────────
Uses the Cloud Asset Inventory API to find every GKE cluster across an entire
GCP organisation in a single paginated API call — no per-project fan-out needed.

Prerequisites
─────────────
pip install google-cloud-asset

Authentication
──────────────
gcloud auth application-default login
— or —
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json

Required IAM (grant at org level)
──────────────────────────────────
roles/cloudasset.viewer
— this alone is sufficient; no Container API or Resource Manager roles needed.

```
gcloud organizations add-iam-policy-binding ORG_ID \
    --member="user:you@example.com" \
    --role="roles/cloudasset.viewer"
```

Usage
─────
python list_gke_projects.py –org 123456789012
python list_gke_projects.py –org 123456789012 –json
python list_gke_projects.py –org 123456789012 –json > gke_projects.json
“””

import argparse
import json
import sys
from collections import defaultdict

from google.cloud import asset_v1

# ── Core query ────────────────────────────────────────────────────────────────

def find_gke_clusters(org_id: str) -> list[dict]:
“””
Search the Cloud Asset Inventory for all GKE cluster resources under the org.

```
The asset type 'container.googleapis.com/Cluster' covers every cluster in
every project and folder beneath the org root — no folder traversal needed.
The search returns one resource record per cluster, each carrying enough
metadata to identify its project, location, and status without any
follow-up API calls.
"""
client = asset_v1.AssetServiceClient()

request = asset_v1.SearchAllResourcesRequest(
    scope=f"organizations/{org_id}",
    asset_types=["container.googleapis.com/Cluster"],
    # read_mask controls which fields come back; omitting it returns everything
    # but these are the fields we actually need.
    read_mask="name,displayName,location,project,state,additionalAttributes",
)

clusters = []
for resource in client.search_all_resources(request=request):
    # resource.name is the full asset name:
    #   //container.googleapis.com/projects/PROJECT_ID/locations/LOCATION/clusters/CLUSTER_NAME
    # Splitting by "/" yields:
    #   [0]=""  [1]=""  [2]="container.googleapis.com"
    #   [3]="projects"  [4]=PROJECT_ID
    #   [5]="locations" [6]=LOCATION
    #   [7]="clusters"  [8]=CLUSTER_NAME
    #
    # NOTE: resource.project returns projects/{project_number} (numeric), NOT
    # the project ID. Always parse project_id from resource.name instead.
    parts        = resource.name.split("/")
    project_id   = parts[4]
    location     = parts[6]
    cluster_name = parts[8]

    # additionalAttributes carries cluster-specific fields like currentMasterVersion,
    # currentNodeCount, and status that are not in the top-level resource schema.
    attrs = dict(resource.additional_attributes) if resource.additional_attributes else {}

    clusters.append({
        "project_id":   project_id,
        "cluster_name": cluster_name,
        "location":     location,
        "display_name": resource.display_name or cluster_name,
        "state":        resource.state.name if resource.state else "UNKNOWN",
        "version":      attrs.get("currentMasterVersion", "—"),
        "node_count":   attrs.get("currentNodeCount", "—"),
    })

return clusters
```

# ── Group by project ──────────────────────────────────────────────────────────

def group_by_project(clusters: list[dict]) -> list[dict]:
“”“Collapse the flat cluster list into a per-project structure.”””
projects: dict[str, dict] = defaultdict(lambda: {“clusters”: []})

```
for c in clusters:
    pid = c["project_id"]
    projects[pid]["project_id"] = pid
    projects[pid]["clusters"].append({
        "cluster_name": c["cluster_name"],
        "location":     c["location"],
        "display_name": c["display_name"],
        "state":        c["state"],
        "version":      c["version"],
        "node_count":   c["node_count"],
    })

return sorted(projects.values(), key=lambda x: x["project_id"])
```

# ── Output formatters ─────────────────────────────────────────────────────────

def print_table(projects: list[dict], total_clusters: int) -> None:
if not projects:
print(“No GKE clusters found in the organisation.”)
return

```
col     = "{:<35} {:<30} {:<25} {:<12} {:>6}  {}"
header  = col.format("PROJECT ID", "CLUSTER NAME", "LOCATION", "STATE", "NODES", "VERSION")
divider = "-" * len(header)

print(header)
print(divider)

for project in projects:
    for i, cluster in enumerate(project["clusters"]):
        pid = project["project_id"] if i == 0 else ""
        print(col.format(
            pid,
            cluster["cluster_name"],
            cluster["location"],
            cluster["state"],
            str(cluster["node_count"]),
            cluster["version"],
        ))

print(divider)
print(f"\nSummary: {len(projects)} project(s) · {total_clusters} cluster(s) total.")
```

def print_json(projects: list[dict]) -> None:
print(json.dumps(projects, indent=2))

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
parser = argparse.ArgumentParser(
description=“List all GCP projects with GKE clusters using the Cloud Asset API.”
)
parser.add_argument(
“–org”,
required=True,
metavar=“ORG_ID”,
help=“Numeric GCP organisation ID (e.g. 123456789012)”,
)
parser.add_argument(
“–json”,
action=“store_true”,
dest=“as_json”,
help=“Output results as JSON instead of a table”,
)
return parser.parse_args()

def main() -> None:
args = parse_args()

```
print(f"Querying Cloud Asset Inventory for org {args.org} ...", file=sys.stderr)

clusters = find_gke_clusters(args.org)
projects = group_by_project(clusters)
total_clusters = len(clusters)

print(f"Found {total_clusters} cluster(s) across {len(projects)} project(s).\n", file=sys.stderr)

if args.as_json:
    print_json(projects)
else:
    print_table(projects, total_clusters)
```

if **name** == “**main**”:
main()
