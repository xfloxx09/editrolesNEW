import re


_SPACE_RE = re.compile(r"\s+")


def format_person_name(raw_name):
    """
    Format a person name as "Nachname Vorname".
    Keeps single-token values unchanged.
    """
    if raw_name is None:
        return ""

    cleaned = _SPACE_RE.sub(" ", str(raw_name)).strip()
    if not cleaned:
        return ""

    # Normalize "Nachname, Vorname" to "Nachname Vorname".
    if "," in cleaned:
        left, right = [part.strip() for part in cleaned.split(",", 1)]
        if left and right:
            cleaned = f"{left} {right}"

    tokens = cleaned.split(" ")
    if len(tokens) <= 1:
        return cleaned

    surname = tokens[-1]
    first_names = " ".join(tokens[:-1])
    return f"{surname} {first_names}".strip()
