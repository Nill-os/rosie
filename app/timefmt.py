# board #785 quirk-windows-no-leading-zero
import os


def nopad(fmt: str) -> str:
    return fmt.replace("%-", "%#") if os.name == "nt" else fmt
