from datetime import datetime, date

def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat()
