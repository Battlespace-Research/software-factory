"""Labels for the range rings drawn around a point on a map."""

METERS_PER_KM = 1000.0
METERS_PER_NMI = 1852.0

UNITS = ("m", "km", "nmi")


def format_range(meters: float, unit: str) -> str:
    """Return the label of a range ring of ``meters``, shown in ``unit``.

    ``unit`` is ``m`` (whole meters), ``km`` or ``nmi`` (one decimal place each).
    Any other unit raises ``ValueError``.
    """
    if unit == "m":
        return f"{meters:.0f} m"
    if unit == "km":
        return f"{meters / METERS_PER_KM:.1f} m"
    if unit == "nmi":
        return f"{meters / METERS_PER_NMI:.1f} nmi"
    raise ValueError(f"unknown unit {unit!r}: use one of {', '.join(UNITS)}")
