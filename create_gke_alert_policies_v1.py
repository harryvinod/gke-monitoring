"""
create_gke_alert_policies.py
────────────────────────────
Creates GCP Monitoring alert policies in a central hub project for multi-project
GKE clusters that have Cloud Monitoring + Managed Service for Prometheus enabled.

All PromQL queries rely on the `monitored_resource` label selector so that alerts
automatically cover every cluster/node that forwards metrics to the hub project —
no hardcoded cluster or node names needed.

Prerequisites
─────────────
    pip install google-cloud-monitoring google-auth

Authentication
──────────────
    Either Application Default Credentials (gcloud auth application-default login)
    or set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON key that has
    "Monitoring AlertPolicy Editor" on the hub project.

Usage
─────
    1. Fill in the CONFIG block below.
    2. python create_gke_alert_policies.py
"""

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG — edit these values before running
# ──────────────────────────────────────────────────────────────────────────────
HUB_PROJECT_ID = "YOUR_HUB_PROJECT_ID"                          # hub GCP project
PAGERDUTY_CHANNEL_ID = "YOUR_PAGERDUTY_NOTIFICATION_CHANNEL_ID" # numeric channel id

# Namespaces to watch for pod-level alerts (regex alternation)
WATCHED_NAMESPACES = "payments|auth|api-gateway|data-processing"
# ──────────────────────────────────────────────────────────────────────────────

from google.cloud import monitoring_v3
from google.protobuf.duration_pb2 import Duration
from google.protobuf import wrappers_pb2

# ── Derived constants ──────────────────────────────────────────────────────────
HUB_PROJECT_RESOURCE = f"projects/{HUB_PROJECT_ID}"
PAGERDUTY_CHANNEL    = f"projects/{HUB_PROJECT_ID}/notificationChannels/{PAGERDUTY_CHANNEL_ID}"

# ── GCP console URL templates ─────────────────────────────────────────────────
# ${resource.label.xxx} substitution is only guaranteed in documentation.subject
# and documentation.content (GCP documented behaviour). documentation.links[].url
# does NOT support substitution — template strings are passed through as literals,
# producing broken hrefs. We therefore use:
#   • _*_URL (with template vars) → embedded as Markdown links inside content only
#   • _*_URL_STATIC (plain base URL) → used in documentation.links[].url
_CLUSTER_URL = (
    "https://console.cloud.google.com/kubernetes/clusters/details"
    "/${resource.label.location}/${resource.label.cluster_name}/details"
    "?project=${resource.label.project_id}"
)
_CLUSTER_URL_STATIC = "https://console.cloud.google.com/kubernetes/clusters"

_NODE_URL = (
    "https://console.cloud.google.com/kubernetes/node"
    "/${resource.label.location}/${resource.label.cluster_name}/${resource.label.node_name}/summary"
    "?project=${resource.label.project_id}"
)
_NODE_URL_STATIC = "https://console.cloud.google.com/kubernetes/node"

_MONITORING_URL = (
    "https://console.cloud.google.com/monitoring/dashboards"
    "?project=${resource.label.project_id}"
)
_MONITORING_URL_STATIC = "https://console.cloud.google.com/monitoring/dashboards"

_WORKLOADS_URL = (
    "https://console.cloud.google.com/kubernetes/workload"
    "?project=${resource.label.project_id}"
)
_WORKLOADS_URL_STATIC = "https://console.cloud.google.com/kubernetes/workload"

def _node_doc(title: str, body: str) -> dict:
    """Documentation block for node-level alerts."""
    return {
        "subject": f"[GKE] {title} | ${{resource.label.cluster_name}} / ${{resource.label.node_name}}",
        "content": (
            f"## {title}\n\n"
            "| Field    | Value |\n"
            "|----------|-------|\n"
            "| **Project**  | `${resource.label.project_id}` |\n"
            "| **Location** | `${resource.label.location}` |\n"
            "| **Cluster**  | `${resource.label.cluster_name}` |\n"
            "| **Node**     | `${resource.label.node_name}` |\n\n"
            f"{body}\n\n"
            "### Quick links\n"
            f"- [Cluster Dashboard]({_CLUSTER_URL})\n"
            f"- [Node Details]({_NODE_URL})\n"
            f"- [Monitoring Dashboards]({_MONITORING_URL})\n"
        ),
        "links": [
            {"display_name": "Cluster Dashboard",    "url": _CLUSTER_URL_STATIC},
            {"display_name": "Node Details",          "url": _NODE_URL_STATIC},
            {"display_name": "Monitoring Dashboards", "url": _MONITORING_URL_STATIC},
        ],
    }


def _cluster_doc(title: str, body: str) -> dict:
    """Documentation block for cluster-level alerts."""
    return {
        "subject": f"[GKE] {title} | ${{resource.label.cluster_name}}",
        "content": (
            f"## {title}\n\n"
            "| Field    | Value |\n"
            "|----------|-------|\n"
            "| **Project**  | `${resource.label.project_id}` |\n"
            "| **Location** | `${resource.label.location}` |\n"
            "| **Cluster**  | `${resource.label.cluster_name}` |\n\n"
            f"{body}\n\n"
            "### Quick links\n"
            f"- [Cluster Dashboard]({_CLUSTER_URL})\n"
            f"- [Monitoring Dashboards]({_MONITORING_URL})\n"
        ),
        "links": [
            {"display_name": "Cluster Dashboard",    "url": _CLUSTER_URL_STATIC},
            {"display_name": "Monitoring Dashboards", "url": _MONITORING_URL_STATIC},
        ],
    }


def _pod_doc(title: str, body: str) -> dict:
    """Documentation block for pod-level alerts."""
    return {
        "subject": f"[GKE] {title} | ${{resource.label.cluster_name}} / ${{resource.label.namespace_name}}",
        "content": (
            f"## {title}\n\n"
            "| Field        | Value |\n"
            "|--------------|-------|\n"
            "| **Project**  | `${resource.label.project_id}` |\n"
            "| **Location** | `${resource.label.location}` |\n"
            "| **Cluster**  | `${resource.label.cluster_name}` |\n"
            "| **Namespace**| `${resource.label.namespace_name}` |\n"
            "| **Pod**      | `${resource.label.pod_name}` |\n\n"
            f"{body}\n\n"
            "### Quick links\n"
            f"- [Workloads]({_WORKLOADS_URL})\n"
            f"- [Cluster Dashboard]({_CLUSTER_URL})\n"
            f"- [Monitoring Dashboards]({_MONITORING_URL})\n"
        ),
        "links": [
            {"display_name": "Workloads",             "url": _WORKLOADS_URL_STATIC},
            {"display_name": "Cluster Dashboard",     "url": _CLUSTER_URL_STATIC},
            {"display_name": "Monitoring Dashboards", "url": _MONITORING_URL_STATIC},
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Alert definitions
# Each entry: (display_name, promql_query, duration_s, eval_interval_s, doc_dict)
# ──────────────────────────────────────────────────────────────────────────────
ALERT_POLICIES = [

    # ── 1. Node CPU Utilization > 90% ─────────────────────────────────────────
    (
        "GKE Node CPU Utilization > 90%",
        (
            'avg_over_time({'
            '__name__="kubernetes.io/node/cpu/allocatable_utilization",'
            'monitored_resource="k8s_node"'
            '}[5m]) * 100 > 90'
        ),
        300,   # duration: condition must hold for 5 min before firing
        60,    # evaluation interval: evaluate every 60 s
        _node_doc(
            "Node CPU Utilization > 90%",
            "CPU allocatable utilization on the node has exceeded **90%** for 5 minutes.\n\n"
            "**Suggested actions:**\n"
            "- Identify and resource-limit high-CPU pods on this node.\n"
            "- Consider enabling cluster autoscaler or manually scaling the node pool.\n"
            "- Review HPA settings for workloads on this cluster."
        ),
    ),

    # ── 2. Node Memory Utilization > 90% ──────────────────────────────────────
    (
        "GKE Node Memory Utilization > 90%",
        (
            # BUG FIX: include cluster_name, project_id, location in by() so that
            # ${resource.label.xxx} substitutions are populated in notifications.
            # sum by (node_name) alone would drop those labels from the result series.
            'sum by (node_name, cluster_name, project_id, location)('
            '  avg_over_time({'
            '    __name__="kubernetes.io/node/memory/allocatable_utilization",'
            '    monitored_resource="k8s_node"'
            '  }[5m])'
            ') * 100 > 90'
        ),
        300,
        60,
        _node_doc(
            "Node Memory Utilization > 90%",
            "Memory allocatable utilization on the node has exceeded **90%** for 5 minutes.\n\n"
            "**Suggested actions:**\n"
            "- Check for memory leaks or missing memory limits in pods on this node.\n"
            "- Consider evicting low-priority pods or draining/replacing the node.\n"
            "- Review VPA recommendations for workloads in this cluster."
        ),
    ),

    # ── 3. Cluster CPU Utilization > 90% ──────────────────────────────────────
    # Averages per-node utilization across all nodes in the cluster.
    (
        "GKE Cluster CPU Utilization > 90%",
        (
            # BUG FIX: include project_id and location so that ${resource.label.xxx}
            # substitutions work in notifications. avg by (cluster_name) alone would
            # strip those labels from the aggregated result series.
            'avg by (cluster_name, project_id, location)('
            '  avg_over_time({'
            '    __name__="kubernetes.io/node/cpu/allocatable_utilization",'
            '    monitored_resource="k8s_node"'
            '  }[5m])'
            ') * 100 > 90'
        ),
        300,
        60,
        _cluster_doc(
            "Cluster CPU Utilization > 90%",
            "Average CPU allocatable utilization across **all nodes** in the cluster "
            "has exceeded **90%** for 5 minutes.\n\n"
            "**Suggested actions:**\n"
            "- Scale up the node pool or enable cluster autoscaler.\n"
            "- Review cluster-wide CPU requests vs. limits.\n"
            "- Check for runaway workloads consuming disproportionate CPU."
        ),
    ),

    # ── 4. Cluster Memory Utilization > 90% ───────────────────────────────────
    (
        "GKE Cluster Memory Utilization > 90%",
        (
            # BUG FIX: same as cluster CPU — include project_id and location.
            'avg by (cluster_name, project_id, location)('
            '  avg_over_time({'
            '    __name__="kubernetes.io/node/memory/allocatable_utilization",'
            '    monitored_resource="k8s_node"'
            '  }[5m])'
            ') * 100 > 90'
        ),
        300,
        60,
        _cluster_doc(
            "Cluster Memory Utilization > 90%",
            "Average memory allocatable utilization across **all nodes** in the cluster "
            "has exceeded **90%** for 5 minutes.\n\n"
            "**Suggested actions:**\n"
            "- Scale up the node pool or enable cluster autoscaler.\n"
            "- Review memory requests/limits for large workloads.\n"
            "- Investigate pods without memory limits that may be unbounded consumers."
        ),
    ),

    # ── 5. Node Disk (Ephemeral Storage) Usage > 90% ──────────────────────────
    # Uses the kubernetes.io/node/ephemeral_storage/allocatable_utilization metric
    # which is available when GKE metrics are enabled.  Fall back to the
    # node_filesystem ratio (node-exporter style) if this metric is absent.
    (
        "GKE Node Disk Utilization > 90%",
        (
            'avg_over_time({'
            '__name__="kubernetes.io/node/ephemeral_storage/allocatable_utilization",'
            'monitored_resource="k8s_node"'
            '}[5m]) * 100 > 90'
        ),
        300,
        60,
        _node_doc(
            "Node Disk (Ephemeral Storage) Utilization > 90%",
            "Ephemeral storage allocatable utilization on the node has exceeded **90%**.\n\n"
            "**Suggested actions:**\n"
            "- Identify pods writing large amounts of data to ephemeral storage (`kubectl top pods`).\n"
            "- Clear unused images/containers: `docker system prune` or node image GC settings.\n"
            "- Consider migrating stateful workloads to Persistent Volumes."
        ),
    ),

    # ── 6. Pod Memory Utilization > 90% (watched namespaces) ──────────────────
    # working_set_bytes / memory limit per pod.
    (
        "GKE Pod Memory Utilization > 90%",
        (
            f'('
            f'  sum by (pod, namespace, cluster, project_id, location)('
            f'    avg_over_time({{'
            f'      __name__="container_memory_working_set_bytes",'
            f'      monitored_resource="k8s_container",'
            f'      namespace=~"{WATCHED_NAMESPACES}"'
            f'    }}[5m])'
            f'  )'
            f'  /'
            f'  sum by (pod, namespace, cluster, project_id, location)('
            f'    avg_over_time({{'
            f'      __name__="kube_pod_container_resource_limits",'
            # BUG FIX: kube_pod_container_resource_limits is a kube-state-metrics
            # metric with a container label. GMP maps it to k8s_container, not k8s_pod.
            # Using k8s_pod here returns no data → denominator is always empty.
            f'      monitored_resource="k8s_container",'
            f'      namespace=~"{WATCHED_NAMESPACES}",'
            f'      resource="memory"'
            f'    }}[5m])'
            f'  )'
            f') * 100 > 90'
        ),
        300,
        60,
        _pod_doc(
            "Pod Memory Utilization > 90%",
            "A pod's working-set memory has exceeded **90%** of its configured memory limit "
            "in one of the watched namespaces.\n\n"
            f"**Watched namespaces:** `{WATCHED_NAMESPACES.replace('|', '`, `')}`\n\n"
            "**Suggested actions:**\n"
            "- Check for memory leaks: attach a profiler or review recent code changes.\n"
            "- Increase the pod's memory limit if usage is legitimate growth.\n"
            "- Review VPA recommendations for this workload.\n"
            "- Monitor OOMKill events: `kubectl get events --field-selector reason=OOMKilling`."
        ),
    ),

    # ── 7. Frequent Pod/Container Restarts (watched namespaces) ───────────────
    # More than 3 restarts in a 15-minute window signals a crash-loop.
    (
        "GKE Pod Frequent Restarts",
        (
            f'sum by (pod, namespace, cluster, project_id, location)('
            f'  increase({{'
            f'    __name__="kube_pod_container_status_restarts_total",'
            # BUG FIX: kube_pod_container_status_restarts_total has a container label
            # and maps to k8s_container in GMP, not k8s_pod. The k8s_pod filter
            # returns no data, so the alert would never fire.
            f'    monitored_resource="k8s_container",'
            f'    namespace=~"{WATCHED_NAMESPACES}"'
            f'  }}[15m])'
            f') > 3'
        ),
        0,     # duration=0: fire as soon as the condition is true (crash-loop is urgent)
        60,
        _pod_doc(
            "Frequent Pod Restarts Detected",
            "A pod has restarted **more than 3 times** within a 15-minute window — "
            "indicating a probable crash-loop.\n\n"
            f"**Watched namespaces:** `{WATCHED_NAMESPACES.replace('|', '`, `')}`\n\n"
            "**Suggested actions:**\n"
            "- Check logs: `kubectl logs <pod> --previous -n <namespace>`.\n"
            "- Describe pod events: `kubectl describe pod <pod> -n <namespace>`.\n"
            "- Review liveness/readiness probe configuration.\n"
            "- Check for OOMKill, config errors, or missing dependencies."
        ),
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# Helper — build and create a single alert policy
# ──────────────────────────────────────────────────────────────────────────────
def create_alert_policy(
    client: monitoring_v3.AlertPolicyServiceClient,
    display_name: str,
    promql_query: str,
    duration_seconds: int,
    evaluation_interval_seconds: int,
    doc: dict,
) -> monitoring_v3.AlertPolicy:
    """
    Create one Cloud Monitoring alert policy backed by a PromQL condition.

    Parameters
    ──────────
    client                      : AlertPolicyServiceClient
    display_name                : Human-readable policy name
    promql_query                : PromQL expression (must evaluate to a scalar
                                  threshold comparison, e.g. "... > 90")
    duration_seconds            : How long (s) the condition must hold before firing
    evaluation_interval_seconds : How frequently the query is evaluated
    doc                         : Dict with keys: subject, content, links
                                  links is a list of {display_name, url} dicts
    """

    # ── PromQL condition ──────────────────────────────────────────────────────
    pql_condition = monitoring_v3.AlertPolicy.Condition.PrometheusQueryLanguageCondition(
        query=promql_query,
        duration=Duration(seconds=duration_seconds),
        evaluation_interval=Duration(seconds=evaluation_interval_seconds),
    )

    condition = monitoring_v3.AlertPolicy.Condition(
        display_name=f"{display_name} — PromQL Condition",
        condition_prometheus_query_language=pql_condition,   # ← correct oneof field name
    )

    # ── Documentation ─────────────────────────────────────────────────────────
    links = [
        monitoring_v3.AlertPolicy.Documentation.Link(
            display_name=lnk["display_name"],
            url=lnk["url"],
        )
        for lnk in doc.get("links", [])
    ]

    documentation = monitoring_v3.AlertPolicy.Documentation(
        subject=doc.get("subject", display_name),
        content=doc.get("content", ""),
        mime_type="text/markdown",
        links=links,
    )

    # ── Policy ────────────────────────────────────────────────────────────────
    policy = monitoring_v3.AlertPolicy(
        display_name=display_name,
        combiner=monitoring_v3.AlertPolicy.ConditionCombinerType.OR,
        conditions=[condition],
        notification_channels=[PAGERDUTY_CHANNEL],
        documentation=documentation,
        # wrappers_pb2.BoolValue is required here — monitoring_v3.BoolValue does not exist
        enabled=wrappers_pb2.BoolValue(value=True),
        alert_strategy=monitoring_v3.AlertPolicy.AlertStrategy(
            # Auto-close the incident after 30 min of no data / condition clearing
            auto_close=Duration(seconds=1800),
        ),
        user_labels={
            "managed-by": "terraform-python",
            "alert-source": "hub-project",
        },
    )

    response = client.create_alert_policy(
        name=HUB_PROJECT_RESOURCE,
        alert_policy=policy,
    )
    return response


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    client = monitoring_v3.AlertPolicyServiceClient()

    print(f"Creating {len(ALERT_POLICIES)} alert policies in project: {HUB_PROJECT_ID}\n")

    for display_name, query, duration_s, eval_s, doc in ALERT_POLICIES:
        try:
            policy = create_alert_policy(
                client=client,
                display_name=display_name,
                promql_query=query,
                duration_seconds=duration_s,
                evaluation_interval_seconds=eval_s,
                doc=doc,
            )
            print(f"  ✓  {display_name}")
            print(f"     → {policy.name}\n")
        except Exception as exc:
            print(f"  ✗  {display_name}")
            print(f"     ERROR: {exc}\n")

    print("Done.")


if __name__ == "__main__":
    main()
