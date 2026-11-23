# board #3055 why-a0-device-honesty
import re

from helpers.errors import RepairableException
from helpers.extension import Extension

DEVICE_FAIL = re.compile(
    r"\b(?:adb|fastboot|scrcpy|heimdall|mtp|usbmux)\b[^\n]*?command not found"
    r"|command not found[^\n]*\b(?:adb|fastboot|scrcpy|heimdall)\b"
    r"|no devices/emulators found"
    r"|error:\s*(?:device (?:unauthorized|offline|not found)|no devices|more than one device|device '[^']*' not found)"
    r"|INSTALL_FAILED_[A-Z_]+"
    r"|failed to connect to [^\n]*(?:phone|device|:5555)"
    r"|cannot (?:access|stat) '?/sdcard",
    re.I)
NOTED = "rosie_device_honesty"
SCAN_CHARS = 6000
TRUTH = (
    "You have no adb, no USB and no link to the owner's phone or any other device from this container"
    " — the output above proves it. You cannot install, push, sideload or sync anything to a phone,"
    " and you must not pretend to: never print an \"installed\"/\"pushed\" line or any device result a"
    " real command did not produce. Instead: put the file in /host/home, give the owner the exact steps"
    " to install it on the phone themselves, and tell them plainly you can't reach the device from here.")


def recent(logs) -> str:
    out = []
    for i in range(len(logs) - 1, -1, -1):
        if getattr(logs[i], "type", "") == "user":
            break
        out.append(str(getattr(logs[i], "content", "") or ""))
    return "\n".join(out)[:SCAN_CHARS]


class DeviceHonesty(Extension):
    async def execute(self, tool_name: str = "", tool_args=None, **kwargs):
        agent = self.agent
        if not agent or getattr(agent, "number", 0) != 0:
            return
        loop = getattr(agent, "loop_data", None)
        if loop is None or loop.params_persistent.get(NOTED):
            return
        if not DEVICE_FAIL.search(recent(agent.context.log.logs)):
            return
        loop.params_persistent[NOTED] = True
        raise RepairableException(TRUTH)
