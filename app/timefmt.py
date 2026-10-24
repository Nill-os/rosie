"""Times without a leading zero ("7:30 PM", "October 3") on every OS. Linux writes them "%-I" and "%-d"; Windows' C library spells the
same "%#I" and "%#d" and raises "Invalid format string" on the Linux form (owner 2026-10-23, the Windows copy)."""
import os


def nopad(fmt: str) -> str:
    return fmt.replace("%-", "%#") if os.name == "nt" else fmt
