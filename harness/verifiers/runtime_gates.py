"""
런타임 게이트 함수. LLM 없음, 순수 결정적 실행.

run_runtime_phase1(service_name) -> {"passed": bool, "checks": [...]}

컨벤션 (context/conventions.md와 동기화):
  namespace      : gikview
  chart_path     : edge-server/helm/<service>/
  values_files   : values.yaml (필수) + values-dev.yaml (존재 시)
  release_name   : <service>-dev-v1
  label_selector : app.kubernetes.io/name=<service>
  smoke_test     : edge-server/scripts/smoke-test-<service>.sh (없으면 skip)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from harness.tools import helm, kubectl, shell

NAMESPACE = "gikview"


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _result(name: str, status: str, detail: str,
            log_dir: Optional[str] = None, raw: str = "") -> dict:
    log_path = None
    if log_dir:
        p = Path(log_dir) / f"{name}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw or detail, encoding="utf-8")
        log_path = str(p)
    return {"name": name, "status": status, "detail": detail, "log_path": log_path}


def _skip(name: str) -> dict:
    return {"name": name, "status": "skip", "detail": "prior step failed", "log_path": None}


def _from_run(name: str, r: dict, log_dir: Optional[str]) -> dict:
    status = "pass" if r["exit_code"] == 0 else "fail"
    detail = (r["stderr"] or r["stdout"]).strip() or "OK"
    return _result(name, status, detail, log_dir, r["stdout"] + r["stderr"])


# ── 이벤트 파싱 ───────────────────────────────────────────────────────────────

def _parse_warning_events(r: dict, log_dir: Optional[str]) -> dict:
    """kubectl get events JSON에서 최근 5분 이내 Warning 이벤트만 추출."""
    raw = r["stdout"] + r["stderr"]

    if r["exit_code"] != 0:
        detail = raw.strip() or "kubectl get events failed"
        return _result("kubectl_events", "fail", detail, log_dir, raw)

    try:
        items = json.loads(r["stdout"]).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return _result("kubectl_events", "fail", "events JSON parse error", log_dir, raw)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    warnings = []
    for ev in items:
        ts_str = (
            ev.get("lastTimestamp")
            or ev.get("eventTime")
            or (ev.get("series") or {}).get("lastObservedTime")
        )
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= cutoff:
            obj = ev.get("involvedObject", {}).get("name", "?")
            reason = ev.get("reason", "")
            msg = ev.get("message", "")
            warnings.append(f"[{obj}] {reason}: {msg}")

    if warnings:
        detail = f"{len(warnings)} warning(s) in last 5m: " + "; ".join(warnings[:5])
        return _result("kubectl_events", "fail", detail, log_dir, raw)

    return _result("kubectl_events", "pass",
                   f"no warnings in last 5m (total={len(items)})", log_dir, raw)


# ── 메인 게이트 함수 ──────────────────────────────────────────────────────────

def run_runtime_phase1(service_name: str, log_dir: Optional[str] = None) -> dict:
    """
    순서대로 실행. 하나 fail이면 이후 체크는 skip하고 즉시 반환.

    Returns:
        {"passed": bool, "checks": [{"name", "status", "detail", "log_path"}, ...]}
    """
    chart_path = f"edge-server/helm/{service_name}"
    release_name = f"{service_name}-dev-v1"
    label_selector = f"app.kubernetes.io/name={service_name}"
    smoke_test_path = Path(f"edge-server/scripts/smoke-test-{service_name}.sh")

    # values.yaml 필수, values-dev.yaml 있으면 포함
    values_files = [
        vf for vf in [
            f"{chart_path}/values.yaml",
            f"{chart_path}/values-dev.yaml",
        ]
        if Path(vf).exists()
    ]

    checks = []

    # ① helm upgrade --install
    r = helm.upgrade_install(release_name, chart_path, NAMESPACE, values_files)
    checks.append(_from_run("helm_install", r, log_dir))
    if checks[-1]["status"] == "fail":
        checks += [_skip("kubectl_wait"), _skip("kubectl_events"), _skip("smoke_test")]
        return {"passed": False, "checks": checks}

    # ② kubectl wait --for=condition=Ready
    r = kubectl.wait("pods", "Ready", NAMESPACE, label=label_selector, timeout="120s")
    status = "pass" if r["exit_code"] == 0 else "fail"
    detail = (r["stdout"] + r["stderr"]).strip() or "OK"
    checks.append(_result("kubectl_wait", status, detail, log_dir, r["stdout"] + r["stderr"]))
    if status == "fail":
        checks += [_skip("kubectl_events"), _skip("smoke_test")]
        return {"passed": False, "checks": checks}

    # ③ kubectl get events (Warning, 최근 5분)
    r = kubectl.get_events(NAMESPACE, field_selector="type=Warning,involvedObject.kind=Pod")
    checks.append(_parse_warning_events(r, log_dir))
    if checks[-1]["status"] == "fail":
        checks.append(_skip("smoke_test"))
        return {"passed": False, "checks": checks}

    # ④ smoke test (존재 시)
    if smoke_test_path.exists():
        r = shell.run(["bash", str(smoke_test_path)])
        checks.append(_from_run("smoke_test", r, log_dir))
        if checks[-1]["status"] == "fail":
            return {"passed": False, "checks": checks}
    else:
        checks.append(_result("smoke_test", "skip",
                               f"no smoke test at {smoke_test_path}", log_dir))

    return {"passed": True, "checks": checks}
