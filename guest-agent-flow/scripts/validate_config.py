"""Build gate for the served config: the registries must be internally consistent.

Run from guest-agent-flow/:  python3 scripts/validate_config.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.nodes import read_calc, read_evidence, read_facts  # noqa: E402
from app.registry import bundles, catalog, decision_table, freshness, versions  # noqa: E402
from app.mocks import crs, decider as decider_mocks, gdl  # noqa: E402

POSTURES = {"READ", "WRITE"}
FACT_TYPES = set(read_facts.READERS)
CALC_TYPES = set(read_calc.CALCS)
POLICY_TYPES = {read_evidence.EVIDENCE_TYPE}


def errors() -> list[str]:
    out: list[str] = []
    intents = {spec["name"] for spec in catalog()["intents"]}
    risks = {spec["name"]: spec["risk"] for spec in catalog()["intents"]}
    policies = set(freshness()["policies"])
    brands = bundles()["brands"]
    rows = decision_table()["rows"]

    seen: set[str] = set()
    for row in rows:
        rid = row["row"]
        if rid in seen:
            out.append(f"{rid}: duplicate row id")
        seen.add(rid)

        if row["intent"] not in intents | {"any", "__out_of_scope__"}:
            out.append(f"{rid}: intent {row['intent']} is not in the closed catalog")
        if row["posture"] not in POSTURES:
            out.append(f"{rid}: posture {row['posture']} is not one of {sorted(POSTURES)}")
        if row["posture"] == "READ" and row["rung_max"] != 0:
            out.append(f"{rid}: an advisory row may not carry rung_max {row['rung_max']}")
        if row["posture"] == "WRITE" and risks.get(row["intent"]) != "transactional":
            out.append(f"{rid}: a WRITE row needs a transactional intent")
        if row["posture"] == "WRITE" and not row.get("requires_capability"):
            out.append(f"{rid}: a WRITE row must name the capability it needs")
        # Availability is a live read, never a crawled document, and never cached across turns.
        for spec in row.get("evidence", []):
            etype, _, policy = spec.partition(":")
            if etype not in FACT_TYPES | CALC_TYPES | POLICY_TYPES:
                out.append(f"{rid}: no tool produces evidence type {etype}")
            if policy and policy not in policies:
                out.append(f"{rid}: unknown freshness policy {policy}")
            if etype == "availability" and policy in ("fp_background", "fp_current"):
                out.append(f"{rid}: availability may not be served on policy {policy}")
        if row["posture"] == "WRITE" and "availability:fp_session" not in row.get("evidence", []):
            out.append(f"{rid}: a booking row must read availability this turn")

        for key in ("max_steps", "max_tools", "max_tokens", "deadline_ms"):
            if key not in row["budgets"]:
                out.append(f"{rid}: budget {key} is missing")

    guards = [n for n, row in enumerate(rows) if row["intent"] in ("any", "__out_of_scope__")]
    ordinary = [n for n, row in enumerate(rows) if row["intent"] not in ("any", "__out_of_scope__")]
    if guards and ordinary and max(guards) > min(ordinary):
        out.append("guard rows must precede ordinary rows in a first-match-wins table")

    for brand_id, bundle in brands.items():
        for intent, rung in bundle["rungs"].items():
            if intent not in intents:
                out.append(f"{brand_id}: rung set for unknown intent {intent}")
            if not 0 <= rung <= 4:
                out.append(f"{brand_id}: rung {rung} for {intent} is out of range")
        for row in rows:
            cap = row.get("requires_capability")
            if row["posture"] != "WRITE" or cap not in bundle["capabilities"]:
                continue
            if bundle["rungs"].get(row["intent"], 0) > row["rung_max"]:
                out.append(f"{brand_id}: bundle rung exceeds {row['row']} rung_max")

    lo_high, hi_high = catalog()["bands"]["HIGH"]
    lo_med, hi_med = catalog()["bands"]["MEDIUM"]
    lo_low, hi_low = catalog()["bands"]["LOW"]
    if not (lo_low == 0.0 and hi_low == lo_med and hi_med == lo_high and hi_high > 1.0):
        out.append("confidence bands must partition [0, 1]")

    # I8: a decider may only be served where its calibration was measured on THIS choice set.
    configured = decision_table().get("deciders") or {}
    calibration = decider_mocks.calibration()
    named = [configured.get("default", "slm_incumbent"), *(configured.get("by_brand") or {}).values()]
    for brand_id in (configured.get("by_brand") or {}):
        if brand_id not in brands:
            out.append(f"deciders: {brand_id} has no bundle")
    for name in dict.fromkeys(named):
        if name not in decider_mocks.IMPLEMENTATIONS:
            out.append(f"deciders: {name} is not a registered implementation")
        elif catalog()["version"] not in calibration.get(name, {}):
            out.append(f"deciders: {name} has no calibration for {catalog()['version']}")

    # Every brand is a configured cell of the same machinery: no property may be orphaned,
    # and no property may be sold by a brand that does not own it.
    owned = {pid: brand_id for brand_id, row in gdl.brands().items()
             if isinstance(row, dict) for pid in row.get("properties", [])}
    for pid, prop in crs.properties().items():
        if owned.get(pid) != prop["brand_id"]:
            out.append(f"{pid}: property brand does not match the brand's property list")
    for pid, brand_id in owned.items():
        if brand_id not in brands:
            out.append(f"{pid}: owned by {brand_id}, which has no bundle")

    for offer in crs.offers():
        if offer["property_id"] not in crs.properties():
            out.append(f"{offer['offer_id']}: offer for unknown property")

    return out


def main() -> int:
    found = errors()
    for message in found:
        print(f"FAIL {message}")
    if found:
        return 1
    print("OK", " ".join(f"{k}={v}" for k, v in versions().items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
