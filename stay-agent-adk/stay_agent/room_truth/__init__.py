"""Room truth: floor plan in, versioned attributes with provenance out.

Extraction proposes, validation is deterministic, the gate either commits or escalates,
and only `commit.py` writes (I6). There is no path from a model's output to the attribute
store that does not pass through all four.
"""
