import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import a0


EXT = Path(a0.__file__).parent / "agent_zero_ext" / "tool_execute_before" / "_25_honesty.py"


class Repairable(Exception):
    pass


def load():
    errors = types.ModuleType("helpers.errors")
    errors.RepairableException = Repairable
    extension = types.ModuleType("helpers.extension")
    extension.Extension = type("Extension", (), {"__init__": lambda self, agent=None: setattr(self, "agent", agent)})
    helpers = types.ModuleType("helpers")
    for name, mod in (("helpers", helpers), ("helpers.errors", errors), ("helpers.extension", extension)):
        sys.modules[name] = mod
    spec = importlib.util.spec_from_file_location("honesty_ext", EXT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def item(kind, content=""):
    return types.SimpleNamespace(type=kind, content=content, heading="", kvps=None)


def agent(logs, number=0):
    loop = types.SimpleNamespace(iteration=1, params_persistent={}, params_temporary={})
    return types.SimpleNamespace(number=number, loop_data=loop,
                                 context=types.SimpleNamespace(log=types.SimpleNamespace(logs=logs)))


def run(mod, ag, tool="code_execution_tool"):
    asyncio.run(mod.DeviceHonesty(ag).execute(tool_name=tool, tool_args={"code": "x"}))


def test_device_unreachable_forces_the_truth_and_blocks_a_fake_push():
    mod = load()
    for out in ("bash: adb: command not found", "error: no devices/emulators found",
                "adb: failed to connect to the phone", "INSTALL_FAILED_NO_CERTIFICATES"):
        ag = agent([item("user", "install the apk on my phone"), item("code_exe", out)])
        raised = False
        try:
            run(mod, ag)
        except Repairable as e:
            raised = True
            assert "no adb" in str(e) and "/host/home" in str(e)
            assert ag.loop_data.params_persistent.get("rosie_device_honesty") is True
        assert raised, out
    print("PASS: device-unreachable output forces the truth and sets the once-flag")


def test_normal_work_and_subordinates_are_left_alone():
    mod = load()
    run(mod, agent([item("user", "list my tasks"), item("code_exe", "3 tasks due today")]))
    run(mod, agent([item("user", "install the apk"), item("code_exe", "adb: command not found")], number=1))
    ag = agent([item("user", "push it"), item("code_exe", "adb: command not found")])
    ag.loop_data.params_persistent["rosie_device_honesty"] = True
    run(mod, ag)
    print("PASS: clean output, subordinate agents, and an already-noted turn do not raise")
