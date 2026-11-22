"""Policy checks on the rendered k3s manifests (both environments), run before anything is deployed.

They enforce the coexistence and least-privilege rules from ARCHITECTURE §13.3 and §10, so a
future change cannot silently add a NodePort, a root container, an unpinned image or a resource
in the other project's namespace.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
import yaml

from src.core.config import REPO_ROOT

pytestmark = pytest.mark.skipif(
    shutil.which("kubectl") is None, reason="kubectl not installed (source scripts/env.sh)"
)

ENVIRONMENTS = {"staging": "ai-lab-staging", "prod": "ai-lab"}
TAG = "0123456789abcdef0123456789abcdef01234567"
WORKLOADS = {"Deployment", "StatefulSet", "CronJob"}


def render(env: str) -> list[dict[str, Any]]:
    out = subprocess.run(  # noqa: S603 - fixed repository script, no user input
        [str(REPO_ROOT / "scripts" / "deploy.sh"), "render", env, TAG],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


@pytest.fixture(scope="module", params=list(ENVIRONMENTS))
def manifests(request: pytest.FixtureRequest) -> tuple[str, list[dict[str, Any]]]:
    return request.param, render(request.param)


def pod_spec(obj: dict[str, Any]) -> dict[str, Any]:
    if obj["kind"] == "CronJob":
        spec: dict[str, Any] = obj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        return spec
    spec = obj["spec"]["template"]["spec"]
    return spec


def containers(obj: dict[str, Any]) -> Iterator[dict[str, Any]]:
    spec = pod_spec(obj)
    yield from spec.get("initContainers", [])
    yield from spec["containers"]


def workloads(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if d["kind"] in WORKLOADS]


def test_everything_lives_in_the_lab_namespace(manifests: tuple[str, list[dict[str, Any]]]) -> None:
    env, docs = manifests
    assert docs
    assert {d["metadata"].get("namespace") for d in docs} == {ENVIRONMENTS[env]}


def test_no_secrets_in_manifests(manifests: tuple[str, list[dict[str, Any]]]) -> None:
    _, docs = manifests
    assert not [d for d in docs if d["kind"] == "Secret"]  # created by create-secrets.sh only


def test_no_host_or_lan_exposure(manifests: tuple[str, list[dict[str, Any]]]) -> None:
    _, docs = manifests
    for svc in (d for d in docs if d["kind"] == "Service"):
        assert svc["spec"].get("type", "ClusterIP") == "ClusterIP", svc["metadata"]["name"]
    for w in workloads(docs):
        spec = pod_spec(w)
        assert not spec.get("hostNetwork") and not spec.get("hostPID")
        for c in containers(w):
            assert not any("hostPort" in p for p in c.get("ports", []))
    quota = next(d for d in docs if d["kind"] == "ResourceQuota")
    assert quota["spec"]["hard"]["services.nodeports"] == "0"
    assert quota["spec"]["hard"]["services.loadbalancers"] == "0"


def test_containers_are_hardened_and_bounded(manifests: tuple[str, list[dict[str, Any]]]) -> None:
    _, docs = manifests
    for w in workloads(docs):
        spec = pod_spec(w)
        name = w["metadata"]["name"]
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault", name
        assert spec["automountServiceAccountToken"] is False, name
        for c in containers(w):
            sc = c["securityContext"]
            assert sc["allowPrivilegeEscalation"] is False, (name, c["name"])
            assert sc["readOnlyRootFilesystem"] is True, (name, c["name"])
            assert sc["capabilities"]["drop"] == ["ALL"], (name, c["name"])
            assert {"cpu", "memory"} <= set(c["resources"]["requests"]), (name, c["name"])
            assert {"cpu", "memory"} <= set(c["resources"]["limits"]), (name, c["name"])


def test_quota_fits_the_declared_limits(manifests: tuple[str, list[dict[str, Any]]]) -> None:
    """Pods would be rejected at deploy time if their limits exceeded the namespace quota."""
    _, docs = manifests

    def cpu(v: str) -> float:
        return float(v[:-1]) / 1000 if v.endswith("m") else float(v)

    total = 0.0
    for w in workloads(docs):
        spec = pod_spec(w)
        main = sum(cpu(c["resources"]["limits"]["cpu"]) for c in spec["containers"])
        init = max(
            (cpu(c["resources"]["limits"]["cpu"]) for c in spec.get("initContainers", [])),
            default=0,
        )
        total += max(main, init)  # what Kubernetes charges against the quota per pod
    quota = next(d for d in docs if d["kind"] == "ResourceQuota")
    assert total <= cpu(quota["spec"]["hard"]["limits.cpu"])


def test_images_are_pinned(manifests: tuple[str, list[dict[str, Any]]]) -> None:
    _, docs = manifests
    for w in workloads(docs):
        for c in containers(w):
            image = c["image"]
            if image.startswith("localhost/ai-lab-"):
                assert image.endswith(f":{TAG}"), image  # exact commit, set by deploy.sh
                assert c["imagePullPolicy"] == "Never"  # imported locally, never pulled
            else:
                tag = image.rsplit(":", 1)[-1]
                assert (
                    ":" in image and tag not in {"latest", ""} and any(ch.isdigit() for ch in tag)
                ), image


def test_network_is_default_deny_without_internet_egress(
    manifests: tuple[str, list[dict[str, Any]]],
) -> None:
    _, docs = manifests
    policies = {d["metadata"]["name"]: d for d in docs if d["kind"] == "NetworkPolicy"}
    deny = policies["default-deny"]["spec"]
    assert deny["podSelector"] == {} and set(deny["policyTypes"]) == {"Ingress", "Egress"}
    for p in policies.values():
        for rule in p["spec"].get("egress", []):
            for peer in rule.get("to", []):
                assert "ipBlock" not in peer  # no internet or LAN egress in Phase 1
                assert "namespaceSelector" not in peer or peer["namespaceSelector"] == {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                }  # only DNS may leave the namespace


def test_dashboard_cluster_ips_are_distinct_and_in_the_service_range() -> None:
    ips = {}
    for env in ENVIRONMENTS:
        svc = next(
            d for d in render(env) if d["kind"] == "Service" and d["metadata"]["name"] == "frontend"
        )
        ips[env] = ipaddress.ip_address(svc["spec"]["clusterIP"])
    assert ips["staging"] != ips["prod"]
    assert all(ip in ipaddress.ip_network("10.43.0.0/16") for ip in ips.values())


def test_volumes_bind_only_to_the_lab_claims() -> None:
    """Every lab claim has exactly one pre-bound static volume on the dedicated lab LV."""
    cluster = [
        d for d in yaml.safe_load_all((REPO_ROOT / "k8s/cluster/storage.yaml").read_text()) if d
    ]
    pvs = {d["metadata"]["name"]: d for d in cluster if d["kind"] == "PersistentVolume"}
    for env, ns in ENVIRONMENTS.items():
        docs = render(env)
        claims = {d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"}
        claims |= {
            f"{t['metadata']['name']}-{s['metadata']['name']}-0"
            for s in docs
            if s["kind"] == "StatefulSet"
            for t in s["spec"]["volumeClaimTemplates"]
        }
        bound = {
            pv["spec"]["claimRef"]["name"]
            for pv in pvs.values()
            if pv["spec"]["claimRef"]["namespace"] == ns
        }
        assert claims == bound, (env, claims, bound)
    for pv in pvs.values():
        assert pv["spec"]["local"]["path"].startswith("/srv/ai-lab/")
        assert pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
        assert pv["spec"]["storageClassName"] == "ai-lab-local"


def test_render_rejects_a_non_sha_tag() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repository script, no user input
        [str(REPO_ROOT / "scripts" / "deploy.sh"), "render", "prod", "latest"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode != 0 and "tag must be a git SHA" in result.stderr


# --- the runner's deploy identity (k8s/cluster/deployer.yaml) -------------------------------


def _deployer() -> list[dict[str, Any]]:
    text = (REPO_ROOT / "k8s/cluster/deployer.yaml").read_text()
    return [d for d in yaml.safe_load_all(text) if d]


def test_deployer_is_bound_only_inside_the_lab_namespaces() -> None:
    docs = _deployer()
    bindings = [d for d in docs if d["kind"] == "RoleBinding"]
    assert {b["metadata"]["namespace"] for b in bindings} == set(ENVIRONMENTS.values())
    cluster_bindings = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    assert [b["roleRef"]["name"] for b in cluster_bindings] == ["ai-lab-deployer-read"]


def test_deployer_has_no_wildcards_and_reads_only_cluster_wide() -> None:
    roles = {d["metadata"]["name"]: d for d in _deployer() if d["kind"] == "ClusterRole"}
    for role in roles.values():
        for rule in role["rules"]:
            assert "*" not in rule["verbs"] and "*" not in rule["resources"]
            assert "*" not in rule["apiGroups"]
    for rule in roles["ai-lab-deployer-read"]["rules"]:
        assert set(rule["verbs"]) <= {"get", "list"}  # cluster-scoped access is read-only


def test_deployer_cannot_change_secrets_or_escalate() -> None:
    roles = [d for d in _deployer() if d["kind"] == "ClusterRole"]
    for role in roles:
        for rule in role["rules"]:
            if "secrets" in rule["resources"]:
                assert rule["verbs"] == ["get"] and rule["resourceNames"] == ["lab-secrets"]
            assert not {"roles", "rolebindings", "clusterroles", "clusterrolebindings"} & set(
                rule["resources"]
            )
            assert not {"escalate", "bind", "impersonate"} & set(rule["verbs"])


def test_deployer_can_do_everything_deploy_applies() -> None:
    """Every kind in the rendered manifests must be writable by the deployer, or deploys fail."""
    resource_of = {
        "Service": "services",
        "ConfigMap": "configmaps",
        "PersistentVolumeClaim": "persistentvolumeclaims",
        "ResourceQuota": "resourcequotas",
        "LimitRange": "limitranges",
        "Deployment": "deployments",
        "StatefulSet": "statefulsets",
        "CronJob": "cronjobs",
        "NetworkPolicy": "networkpolicies",
    }
    role = next(d for d in _deployer() if d["metadata"]["name"] == "ai-lab-deployer")
    writable = {
        res
        for rule in role["rules"]
        if {"create", "patch"} <= set(rule["verbs"])
        for res in rule["resources"]
    }
    kinds = {d["kind"] for env in ENVIRONMENTS for d in render(env)}
    assert {resource_of[k] for k in kinds} <= writable, kinds
