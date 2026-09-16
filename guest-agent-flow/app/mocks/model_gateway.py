"""Deterministic mock model gateway.

The composer is a template engine over the evidence envelope: it can only restate values
a tool produced, and every sentence carries the item id it came from. Two fault flags
exercise the output gate and the retry path.
"""
import os
import threading

_LOCK = threading.Lock()
_FAULTS_FIRED: set[str] = set()


class ModelTimeout(RuntimeError):
    pass


def reset() -> None:
    with _LOCK:
        _FAULTS_FIRED.clear()


LEAD = {
    "g_property_search_v1": "Here is what is actually available for your dates",
    "g_stay_quote_v1": "Here is the price for that stay",
    "g_loyalty_readonly_v1": "Here is where your membership stands",
    "g_reservation_readonly_v1": "Here is the reservation on file",
    "g_policy_readonly_v1": "Here is what the hotel's own information says",
    "g_booking_corridor_v1": "Here is the stay I would book",
}

NOTHING_FITS = ("No room at this brand matches your dates, party and filters, so I am not "
                "going to suggest one")


def _pairs(payload: dict, prefix: str = "") -> list[str]:
    """Flatten a tool payload into "<label> is <value>" fragments, nested groups included."""
    parts: list[str] = []
    for key, value in payload.items():
        label = f"{prefix}{key}".replace("_", " ")
        if isinstance(value, dict):
            parts.extend(_pairs(value, f"{prefix}{key} "))
        elif isinstance(value, str) and value:
            parts.append(f"{label} is {value}")
        elif isinstance(value, list) and all(isinstance(entry, str) for entry in value) and value:
            parts.append(f"{label} is {', '.join(value)}")
    return parts


def _offers(payload: dict) -> str:
    rooms = [
        f"the {row['room']} at {row['property']} on the {row['rate plan']} rate, "
        f"{row['nightly rate']} a night, breakfast {row['breakfast included']}, "
        f"sleeps {row['sleeps']}, {row['rooms left']} left"
        for key, row in payload.items() if isinstance(row, dict) and key.startswith("offer ")
    ]
    return (f"For {payload['check in']} to {payload['check out']} I can see "
            f"{payload['count']} matching rooms: " + "; ".join(rooms))


def _reservations(payload: dict) -> str:
    if payload.get("count") == "0":
        return "There is no reservation on file under your membership"
    rows = [
        f"{number} at {row['property_id']}, {row['room_name']} on the {row['rate_plan']} rate, "
        f"{row['check_in']} to {row['check_out']}, {row['total_price']} total, "
        f"status {row['status']}"
        for number, row in payload["reservations"].items()
    ]
    return "Your reservations are " + "; ".join(rows)


def _stay_quote(payload: dict) -> str:
    stays = [
        f"{row['room']} at {row['property']} is {row['nightly_rate']} a night, "
        f"{row['room_subtotal']} for the room, {row['taxes_and_fees']} taxes and fees, "
        f"{row['total_price']} in total"
        for key, row in payload.items() if isinstance(row, dict) and key.startswith("stay ")
    ]
    return (f"Priced for {payload['nights']} nights from {payload['check_in']} to "
            f"{payload['check_out']}: " + "; ".join(stays))


def _points_price(payload: dict) -> str:
    return (f"The stay totals {payload['total_price']}, which is "
            f"{payload['points_for_full_stay']} points at {payload['points_per_dollar']} "
            f"points per dollar; your balance of {payload['points_balance']} covers "
            f"{payload['points_applied']} of it, leaving {payload['cash_due']} in cash")


def _sentence(item: dict) -> str:
    payload = item["payload"]
    if item["kind"] == "policy":
        return payload["text"].rstrip(".")
    if item["evidence_type"] == "availability":
        return NOTHING_FITS if payload.get("count") == "0" else _offers(payload)
    if item["evidence_type"] == "reservations":
        return _reservations(payload)
    if item["evidence_type"] == "stay_quote":
        return _stay_quote(payload)
    if item["evidence_type"] == "points_price":
        return _points_price(payload)
    parts = _pairs(payload)
    if item["kind"] == "fact":
        return "Your record shows " + ", ".join(parts) if parts else "Your record was retrieved"
    return "The calculation shows " + ", ".join(parts) if parts else "The calculation returned no values"


def compose(graph_id: str, envelope: list[dict], intent: str) -> dict:
    if os.environ.get("MODEL_TIMEOUT_ONCE") == "1":
        with _LOCK:
            if "model_timeout" not in _FAULTS_FIRED:
                _FAULTS_FIRED.add("model_timeout")
                raise ModelTimeout(graph_id)

    lead = LEAD.get(graph_id, "Here is what the hotel systems show")
    sentences = []
    if envelope:
        sentences.append(f"{lead} [{envelope[0]['item_id']}].")
    for item in envelope:
        sentences.append(f"{_sentence(item)} [{item['item_id']}].")

    uncited = os.environ.get("MODEL_UNCITED_CLAIM")
    if uncited in ("1", "always"):
        with _LOCK:
            if uncited == "always" or "uncited" not in _FAULTS_FIRED:
                _FAULTS_FIRED.add("uncited")
                sentences.append("There is also a rooftop suite at 129.00 a night tonight.")

    return {
        "text": " ".join(sentences),
        "model": "mock.composer.v1",
        "intent": intent,
        "graph_id": graph_id,
    }
