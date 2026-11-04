"""#588：按显式读集核对输入证据；只读诊断，不授权交易或重写全量质检。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    frequency: Literal["daily", "1m", "realtime", "reference"]
    universe: Literal["all_a", "trading", "archive", "index"]
    fields: list[str] = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)
    start: date
    end: date
    # 由实际读取器提供当前输入版本；缺失时明确待核验。
    input_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_scope(self):
        if self.start > self.end or len(set(self.fields)) != len(self.fields) \
                or len(set(self.symbols)) != len(self.symbols) \
                or any(not value.strip() for value in self.fields + self.symbols):
            raise ValueError("invalid or duplicate input scope")
        return self


class InputEvidence(InputScope):
    status: Literal["passed", "warning", "failed", "unknown"]
    run_id: str = Field(min_length=1)
    checked_at: datetime
    expires_at: datetime


class ConsumerInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    consumer: str = Field(min_length=1)
    requirements: list[InputScope] = Field(min_length=1)
    evidence: list[InputEvidence]
    now: datetime


def assess_consumer_inputs(request: ConsumerInputs) -> dict:
    """证据缺失/范围不符/版本变化/过期/警告均不自动放行。

    input_version的真实性仍由实际读取器负责；此函数验证清单中的精确绑定，
    不是对任意输入清单签发生产凭证。历史归档失败单独保留，不抹去或改绿。
    """
    needs = request.requirements
    if len({n.name for n in needs}) != len(needs):
        raise ValueError("duplicate requirement")
    results = []
    for need in needs:
        matches = [e for e in request.evidence if e.name == need.name]
        reasons = []
        evidence = matches[0] if len(matches) == 1 else None
        if evidence is None:
            reasons.append("missing_evidence" if not matches else "ambiguous_evidence")
        else:
            if (need.dataset, need.frequency, need.universe) != (
                evidence.dataset, evidence.frequency, evidence.universe,
            ) or not set(need.fields) <= set(evidence.fields) \
                    or not set(need.symbols) <= set(evidence.symbols) \
                    or evidence.start > need.start or evidence.end < need.end:
                reasons.append("scope_mismatch")
            if need.input_version is None or evidence.input_version is None:
                reasons.append("unbound_input_version")
            elif need.input_version != evidence.input_version:
                reasons.append("input_version_changed")
            stamps = (evidence.checked_at, evidence.expires_at, request.now)
            if len({t.tzinfo is None for t in stamps}) != 1:
                reasons.append("clock_contract_mismatch")
            elif not evidence.checked_at <= request.now < evidence.expires_at:
                reasons.append("evidence_not_current")
            if evidence.status != "passed":
                reasons.append("required_" + evidence.status)
        results.append({"name": need.name, "ready": not reasons, "reasons": reasons,
                        "run_id": evidence.run_id if evidence else None})
    required_names = {n.name for n in needs}
    outside = [{"name": e.name, "status": e.status, "run_id": e.run_id}
               for e in request.evidence if e.name not in required_names]
    return {"consumer": request.consumer, "inputs_ready": all(r["ready"] for r in results),
            "authorizes_trading": False, "required_inputs": results,
            "outside_contract": outside,
            "observed_quality_failed": any(e.status == "failed" for e in request.evidence)}


def main(argv=None) -> int:
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.manifest.stat().st_size > 2_000_000:
            raise ValueError("manifest size limit")
        request = ConsumerInputs.model_validate_json(args.manifest.read_bytes())
        result = assess_consumer_inputs(request)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": "invalid_consumer_input_manifest",
                          "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["inputs_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
