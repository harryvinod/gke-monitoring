"""
list_gke_projects.py
────────────────────
Lists all GCP projects within an organisation that contain at least one
GKE cluster, along with each cluster's name, location, and status.

Prerequisites
─────────────
    pip install google-cloud-resource-manager google-cloud-container

Authentication
──────────────
    gcloud auth application-default login
    — or —
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json

Required IAM permissions
────────────────────────
    resourcemanager.projects.list   (on the org / folder)
    container.clusters.list         (on each project — ideally via
                                     roles/container.clusterViewer at org level)

Usage
─────
    # All projects directly under the org
    python list_gke_projects.py --org YOUR_ORG_ID

    # Limit concurrency and output as JSON
    python list_gke_projects.py --org YOUR_ORG_ID --workers 20 --json

    # Write results to a file
    python list_gke_projects.py --org YOUR_ORG_ID --json > gke_projects.json
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.api_core.exceptions import Forbidden, GoogleAPICallError
from google.cloud import container_v1
from google.cloud import resourcemanager_v3


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_all_projects(org_id: str) -> list[dict]:
    """
    Return every ACTIVE project that lives anywhere under the given org,
    traversing folders recursively via the Resource Manager Search API.
    """
    rm_client = resourcemanager_v3.ProjectsClient()

    # The query filter scopes results to the org and excludes deleted projects.
    request = resourcemanager_v3.SearchProjectsRequest(
        query=f"parent.id:{org_id} state:ACTIVE"
    )

    projects = []
    for project in rm_client.search_projects(request=request):
        projects.append({
            "project_id":     project.project_id,
            "project_number": project.name.split("/")[-1],
            "display_name":   project.display_name,
            "state":          project.state.name,
        })

    return projects


def get_clusters_for_project(project_id: str) -> list[dict]:
    """
    Return all GKE clusters in a project (all locations).
    Returns an empty list if the project has no clusters or the caller lacks
    permission — logs a warning but does not abort the whole scan.
    """
    gke_client = container_v1.ClusterManagerClient()

    try:
        # "-" as the location means "all regions and zones"
        response = gke_client.list_clusters(parent=f"projects/{project_id}/locations/-")
        clusters = []
        for cluster in response.clusters:
            clusters.append({
                "name":           cluster.name,
                "location":       cluster.location,
                "status":         cluster.status.name,
                "version":        cluster.current_master_version,
                "node_count":     cluster.current_node_count,
                "endpoint":       cluster.endpoint,
            })
        return clusters

    except Forbidden:
        print(
            f"  [WARN] Permission denied on project '{project_id}' — skipping.",
            file=sys.stderr,
        )
        return []
    except GoogleAPICallError as exc:
        # Container API not enabled in this project, or project is being deleted, etc.
        if "has not been used" in str(exc) or "is disabled" in str(exc) or "DISABLED" in str(exc):
            return []   # silently skip — Container API simply isn't enabled
        print(
            f"  [WARN] API error for project '{project_id}': {exc.message}",
            file=sys.stderr,
        )
        return []


def scan_org(org_id: str, max_workers: int = 10) -> list[dict]:
    """
    Enumerate every project in the org and fan out GKE cluster lookups
    concurrently. Returns only projects that have at least one cluster.
    """
    print(f"Fetching projects under org {org_id} ...", file=sys.stderr)
    all_projects = get_all_projects(org_id)
    total = len(all_projects)
    print(f"Found {total} active project(s). Scanning for GKE clusters ...\n", file=sys.stderr)

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_project = {
            executor.submit(get_clusters_for_project, p["project_id"]): p
            for p in all_projects
        }

        for future in as_completed(future_to_project):
            project = future_to_project[future]
            completed += 1
            print(
                f"  [{completed}/{total}] {project['project_id']}",
                end="\r",
                file=sys.stderr,
            )

            clusters = future.result()
            if clusters:
                results.append({
                    "project_id":   project["project_id"],
                    "display_name": project["display_name"],
                    "clusters":     clusters,
                })

    # Clear the progress line
    print(" " * 60, end="\r", file=sys.stderr)
    return results


# ── Output formatters ─────────────────────────────────────────────────────────

def print_table(results: list[dict]) -> None:
    if not results:
        print("No GKE clusters found in any project.")
        return

    total_clusters = sum(len(r["clusters"]) for r in results)
    print(f"{'PROJECT ID':<35} {'DISPLAY NAME':<30} {'CLUSTER':<30} {'LOCATION':<25} {'STATUS':<15} {'NODES':>5} {'VERSION'}")
    print("-" * 155)

    for entry in sorted(results, key=lambda x: x["project_id"]):
        for i, cluster in enumerate(entry["clusters"]):
            proj_id   = entry["project_id"]   if i == 0 else ""
            proj_name = entry["display_name"] if i == 0 else ""
            print(
                f"{proj_id:<35} {proj_name:<30} "
                f"{cluster['name']:<30} {cluster['location']:<25} "
                f"{cluster['status']:<15} {cluster['node_count']:>5} "
                f"{cluster['version']}"
            )

    print("-" * 155)
    print(f"\nSummary: {len(results)} project(s) with GKE clusters, {total_clusters} cluster(s) total.")


def print_json(results: list[dict]) -> None:
    print(json.dumps(results, indent=2))


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List all GCP projects in an org that contain GKE clusters."
    )
    parser.add_argument(
        "--org",
        required=True,
        metavar="ORG_ID",
        help="Numeric GCP organisation ID (e.g. 123456789012)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        metavar="N",
        help="Max concurrent project scans (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output results as JSON instead of a table",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = scan_org(org_id=args.org, max_workers=args.workers)

    if args.as_json:
        print_json(results)
    else:
        print_table(results)


if __name__ == "__main__":
    main()
