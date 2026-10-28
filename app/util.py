import datetime as dt


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def now_dt() -> dt.datetime:
    return dt.datetime.now().astimezone()
