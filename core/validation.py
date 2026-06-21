"""Continuity validation: required-category coverage and path-gap detection."""

from .schemas import REQUIRED_EQUIVALENTS


def check_required_categories(frames, required_categories, equivalents=None):
    """Return required categories not covered, honoring equivalence groups."""
    equivalents = equivalents if equivalents is not None else REQUIRED_EQUIVALENTS
    uploaded = {f.get("category", "unknown") for f in frames}
    missing = []
    for c in required_categories:
        group = equivalents.get(c, [c])
        if not any(g in uploaded for g in group):
            missing.append(c)
    return missing


def detect_path_gaps(frames):
    """Flag implausible jumps between unrelated spaces along the ordered path."""
    ordered = sorted(frames, key=lambda f: f.get("order", 9999))
    categories = [f.get("category", "unknown") for f in ordered]
    gaps = []

    for i in range(len(categories) - 1):
        current = categories[i]
        nxt = categories[i + 1]

        if current in ("exterior_approach", "arrival_plaza") and nxt == "lobby_reveal":
            gaps.append({
                "between": [current, nxt],
                "missing": "entrance_threshold",
                "reason": "Exterior to lobby needs a doorway or threshold reference.",
            })

        if current.startswith("lobby") and nxt.startswith("amenity"):
            gaps.append({
                "between": [current, nxt],
                "missing": "interior_transition or corridor_transition",
                "reason": "Lobby to amenity needs a physical transition frame.",
            })

        if current.startswith("amenity") and nxt.startswith("apartment"):
            gaps.append({
                "between": [current, nxt],
                "missing": "corridor_transition, elevator_transition, or apartment_entry",
                "reason": "Amenity to apartment needs a plausible circulation path.",
            })

        if current.startswith("apartment") and nxt.startswith("rooftop"):
            gaps.append({
                "between": [current, nxt],
                "missing": "elevator_transition or rooftop_arrival",
                "reason": "Apartment to rooftop needs a vertical transition reference.",
            })

    return gaps
