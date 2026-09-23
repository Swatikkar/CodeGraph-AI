from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from models.user import RuntimeMetricModel
from utils.ai_runtime import current_ai_run
from utils.database import SessionLocal
from utils.observability import log_event


def _pricing() -> dict:
    try:
        value = json.loads(settings.AI_PROVIDER_PRICING_JSON or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def estimate_cost_microusd(component: str, input_tokens: int | None, output_tokens: int | None) -> int | None:
    rates = _pricing().get(component)
    if not isinstance(rates, dict):
        return None
    try:
        input_rate = float(rates.get("input", 0))
        output_rate = float(rates.get("output", 0))
    except (TypeError, ValueError):
        return None
    # USD per million tokens converts directly to micro-USD per token.
    return round((input_tokens or 0) * input_rate + (output_tokens or 0) * output_rate)


def record_runtime_metric(
    *,
    kind: str,
    component: str,
    status: str,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    token_source: str | None = None,
    prompt_id: str | None = None,
    prompt_version: str | None = None,
    error_type: str | None = None,
    attributes: dict | None = None,
) -> None:
    context = current_ai_run()
    trace_id = context.trace_id if context else "unscoped"
    user_id = context.user_id if context else "system"
    project_slug = context.project_slug if context else None
    cost = estimate_cost_microusd(component, input_tokens, output_tokens)
    safe_attributes = attributes or {}
    log_event(
        "runtime_metric",
        trace_id=trace_id,
        metric_kind=kind,
        component=component,
        status=status,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        token_source=token_source,
        estimated_cost_microusd=cost,
        error_type=error_type,
        **safe_attributes,
    )
    if context:
        context.metrics.append({
            "trace_id": trace_id,
            "user_id": user_id,
            "project_slug": project_slug,
            "kind": kind,
            "component": component,
            "status": status,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_source": token_source,
            "estimated_cost_microusd": cost,
            "error_type": error_type,
            "attributes_json": json.dumps(safe_attributes, separators=(",", ":"), default=str),
        })


def flush_runtime_metrics(context) -> None:
    if not context.metrics:
        return
    db = SessionLocal()
    try:
        db.add_all(RuntimeMetricModel(**payload) for payload in context.metrics)
        db.commit()
    except Exception as exc:
        db.rollback()
        log_event("runtime_metric_persist_failed", trace_id=context.trace_id, error_type=type(exc).__name__)
    finally:
        db.close()
        context.metrics.clear()


def summarize_runtime_metrics(db: Session, user_id: int | str, days: int = 7) -> dict:
    bounded_days = max(1, min(days, settings.AI_METRICS_RETENTION_DAYS))
    since = datetime.now(timezone.utc) - timedelta(days=bounded_days)
    rows = (
        db.query(
            RuntimeMetricModel.kind,
            RuntimeMetricModel.component,
            RuntimeMetricModel.status,
            func.count(RuntimeMetricModel.id),
            func.avg(RuntimeMetricModel.latency_ms),
            func.coalesce(func.sum(RuntimeMetricModel.input_tokens), 0),
            func.coalesce(func.sum(RuntimeMetricModel.output_tokens), 0),
            func.coalesce(func.sum(RuntimeMetricModel.estimated_cost_microusd), 0),
            func.count(RuntimeMetricModel.estimated_cost_microusd),
        )
        .filter(RuntimeMetricModel.user_id == str(user_id), RuntimeMetricModel.created_at >= since)
        .group_by(RuntimeMetricModel.kind, RuntimeMetricModel.component, RuntimeMetricModel.status)
        .all()
    )
    groups = []
    provider_totals: dict[str, dict[str, int]] = {}
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_microusd = 0
    priced_calls_total = 0
    for kind, component, status, count, avg_latency, input_tokens, output_tokens, cost_micro, priced_calls in rows:
        total_input_tokens += int(input_tokens)
        total_output_tokens += int(output_tokens)
        total_cost_microusd += int(cost_micro)
        priced_calls_total += int(priced_calls)
        if kind == "provider":
            totals = provider_totals.setdefault(component, {"success": 0, "failed": 0, "skipped": 0})
            totals[status if status in totals else "failed"] += int(count)
        groups.append({
            "kind": kind,
            "component": component,
            "status": status,
            "count": count,
            "average_latency_ms": round(float(avg_latency), 2) if avg_latency is not None else None,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "estimated_cost_usd": round(int(cost_micro) / 1_000_000, 6) if priced_calls else None,
            "priced_calls": int(priced_calls),
        })
    provider_summary = []
    for component, totals in sorted(provider_totals.items()):
        attempted = totals["success"] + totals["failed"]
        provider_summary.append({
            "component": component,
            **totals,
            "success_rate": round(totals["success"] / attempted, 4) if attempted else None,
        })

    answer_rows = db.query(RuntimeMetricModel.attributes_json).filter(
        RuntimeMetricModel.user_id == str(user_id),
        RuntimeMetricModel.created_at >= since,
        RuntimeMetricModel.kind == "answer",
        RuntimeMetricModel.status == "success",
    ).all()
    answers_with_references = 0
    for (attributes_json,) in answer_rows:
        try:
            if int(json.loads(attributes_json or "{}").get("reference_count", 0)) > 0:
                answers_with_references += 1
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    answer_count = len(answer_rows)
    return {
        "window_days": bounded_days,
        "totals": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cost_usd": round(total_cost_microusd / 1_000_000, 6) if priced_calls_total else None,
            "priced_calls": priced_calls_total,
        },
        "provider_summary": provider_summary,
        "answer_evidence": {
            "answers": answer_count,
            "answers_with_references": answers_with_references,
            "reference_coverage": round(answers_with_references / answer_count, 4) if answer_count else None,
        },
        "groups": groups,
    }


def prune_runtime_metrics(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.AI_METRICS_RETENTION_DAYS)
    deleted = db.query(RuntimeMetricModel).filter(RuntimeMetricModel.created_at < cutoff).delete()
    db.commit()
    return deleted
