"""Thai text helpers."""
THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
_TO_ARABIC = str.maketrans(THAI_DIGITS, "0123456789")


def to_arabic(s: str) -> str:
    """Convert Thai digits to Arabic digits, leaving everything else unchanged."""
    return s.translate(_TO_ARABIC)


def thai_int(s: str) -> int:
    """Parse a number written in Thai and/or Arabic digits."""
    return int(to_arabic(s.strip()))
