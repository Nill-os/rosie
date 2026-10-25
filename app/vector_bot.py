"""Rosie's hands on Vector: his own SDK port (gRPC on the robot, port 443), the same one the Vector SDK and wire-pod use.

Authorization is a client token (GUID) whose salted hash Vector reads from the vic.AppTokens doc on Rosie's Vector
server (vector_pod.py). Vector's TLS certificate is self-signed, so the first one seen is kept and then required
(trust on first use). Behavior control: OVERRIDE_BEHAVIORS borrows him for one action (say a long answer);
"sit" holds at DEFAULT (SIT_PRIORITY) and takes control back whenever he loses it (his wake word, being picked up). SayText
fails with "Failed to say text" under RESERVE_CONTROL (2.0.1.6076ep, 2026-10-21); one action borrows OVERRIDE_BEHAVIORS."""
import queue
import socket
import ssl
import threading
import time

import grpc

from vector_pb.sdk import behavior_pb2, external_interface_pb2_grpc, messages_pb2, settings_pb2, shared_pb2

PORT = 443
CONNECT_S = 6
# Sitting holds control at DEFAULT: his wandering stops, but his wake word, being picked up and other important
# reactions still win (and control comes back by itself after). At OVERRIDE_BEHAVIORS his wake word was heard but thrown
# away ("PendingTriggerNotCleared ... forcing a clear"), so "Hey Rosie" did nothing while he sat (WireOS 3.0.2.34d, 2026-10-21).
SIT_PRIORITY = behavior_pb2.ControlRequest.DEFAULT
# His face menu and controller driving hold at DEFAULT too. At OVERRIDE_BEHAVIORS his wake word is dropped
# ("trigger_word.dropped"), and twice that left his engine waiting forever for the wake word animation
# (WaitingForTriggerWordGetInToFinish): "Hey Rosie" did nothing until his services restarted (2026-10-21 sit, 2026-10-24 14:56).
MENU_PRIORITY = SIT_PRIORITY
DRIVE_PRIORITY = SIT_PRIORITY
FIRST_SDK_TAG, LAST_SDK_TAG = 2000001, 3000000         # his actions (turn, drive straight, head angle) need an id_tag in this range
_tags = iter(range(FIRST_SDK_TAG, LAST_SDK_TAG))
CALM_POWER_MODE = 0x400                              # RobotState.status bit (ROBOT_STATUS_CALM_POWER_MODE): sleeping, camera off
BUTTON_PRESSED = 0x10                                # RobotState.status bit (ROBOT_STATUS_IS_BUTTON_PRESSED)


def fetch_cert(ip: str, port: int = PORT) -> str:
    """Vector's self-signed TLS certificate as PEM (read without checking it: this is the trust-on-first-use step)."""
    return ssl.get_server_certificate((ip, port), timeout=CONNECT_S)


def cert_name(pem: str) -> str:
    """The certificate's common name ('Vector-A1B2'): gRPC must check the certificate against this name, not the IP."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    cert = x509.load_pem_x509_certificate(pem.encode())
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return names[0].value if names else ""


class Robot:
    def __init__(self, ip: str, guid: str, cert_pem: str):
        self.ip = ip
        self.name = cert_name(cert_pem)
        self._pem, self._guid = cert_pem, guid
        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(root_certificates=cert_pem.encode()),
            grpc.access_token_call_credentials(guid))       # "authorization: Bearer <guid>", what vic-gateway expects
        opts = [("grpc.ssl_target_name_override", self.name)] if self.name else []
        self.channel = grpc.secure_channel(f"{ip}:{PORT}", creds, options=opts)
        self.stub = external_interface_pb2_grpc.ExternalInterfaceStub(self.channel)
        self._hold: _Control | None = None
        self._priority = SIT_PRIORITY                   # what the hold asks for (sitting, or his face menu)
        self._want_hold = False                         # Rosie means to hold him (sit, drive, menu) until release()
        self._lock = threading.Lock()
        self.talk_volume: str | None = None              # quiet when idle (Settings > Vector): muted except while he talks
        self._quiet: threading.Timer | None = None
        self._vlock = threading.Lock()
        self._talking = 0                                # Rosie lines being said now: no mute while any is

    def close(self, restore_volume=True):
        """Let go of him and give him his voice back (a mute or a hold must never outlive Rosie's server)."""
        with self._vlock:
            if self._quiet:
                self._quiet.cancel()
        self.release()
        if restore_volume and self.talk_volume:
            try:
                self.set_volume(self.talk_volume)
            except Exception:
                pass
        self.channel.close()

    # control -------------------------------------------------------------------------------------------------------
    @property
    def held(self) -> bool:
        """Sitting: Rosie means to hold him, also while control is away or its stream dropped for a moment (ensure_control
        takes it back). A dropped stream used to count as "not sitting": he stood up after answering while he sat."""
        return bool(self._want_hold and self._hold is not None)

    def hold(self, timeout=8.0, priority=SIT_PRIORITY) -> bool:
        """Keep Vector still: his own behaviors (wandering, exploring, idle animations) stay off until release()."""
        with self._lock:
            if self.held:
                return True
            if self._hold:
                self._hold.stop()
            self._priority = priority
            self._hold = _Control(self.stub, self._priority, keep=True)
            if self._hold.start(timeout):
                self._want_hold = True
                return True
            self._hold.stop()                            # not granted: no half-held robot that refuses to move while he wanders
            self._hold = None
            return False

    def has_control(self) -> bool:
        hold = self._hold
        return bool(hold is not None and hold.granted.is_set())

    def ensure_control(self, after_s: float = 3.0, timeout: float = 5.0) -> bool:
        """While he sits: if control went away (his wake word, being picked up) and did not come back within `after_s`,
        ask again on a fresh stream. A re-ask on the old stream is not always granted (seen on WireOS 3.0.2.34d)."""
        hold = self._hold
        if hold is None:
            return False
        if hold.granted.is_set():
            return True
        if time.time() - hold.lost_at < after_s:
            return False
        with self._lock:
            if self._hold is not hold:
                return self.has_control()
            hold.stop()
            self._hold = _Control(self.stub, self._priority, keep=True)
            return self._hold.start(timeout)

    def rehold(self, timeout: float = 5.0, priority=None) -> bool:
        """Take control again on a fresh stream. His listening takes over his face without a control-lost event (seen
        on WireOS 3.0.2.34d 2026-10-21: Rosie still 'had' control, his eyes were back), so after each voice request
        while he sits, control is asked for anew. `priority` changes what the hold asks for from now on."""
        with self._lock:
            if self._hold is None:
                return False
            if priority is not None:
                self._priority = priority
            self._hold.stop()
            self._hold = _Control(self.stub, self._priority, keep=True)
            return self._hold.start(timeout)

    def release(self):
        with self._lock:
            self._want_hold = False
            if self._hold:
                self._hold.stop()
                self._hold = None

    # actions ---------------------------------------------------------------------------------------------------------
    def borrow(self, timeout=8.0, priority=behavior_pb2.ControlRequest.OVERRIDE_BEHAVIORS):
        """Control for one action: None when he sits (Rosie holds him already), else a granted _Control to stop() after.
        Driving on his own (vector_look.py) borrows at DEFAULT, so his cliff and pick-up reactions still win."""
        hold = self._hold
        if hold is not None and hold.alive():
            hold.granted.wait(timeout)                      # control may be coming back after his wake word
            return None
        ctl = _Control(self.stub, priority)
        if not ctl.start(timeout):
            ctl.stop()
            raise TimeoutError("Vector did not hand over control")
        return ctl

    def _speaking(self, fn, ctl="borrow"):
        """Run fn() with his voice up and control in hand; mute again after (quiet when idle)."""
        with self._vlock:
            self._talking += 1
        self.loud()
        own = self.borrow() if ctl == "borrow" else ctl
        try:
            return fn()
        finally:
            if own is not None:
                own.stop()
            with self._vlock:
                self._talking -= 1
            self.quiet_after(1.5)

    def say(self, text: str, timeout=60.0, ctl="borrow"):
        """Vector says `text` in his own voice."""
        req = messages_pb2.SayTextRequest(text=text, use_vector_voice=True, duration_scalar=1.0)
        return self._speaking(lambda: self.stub.SayText(req, timeout=timeout), ctl)

    def play(self, pcm: bytes, rate: int = 16000, volume: int = 100, ctl="borrow"):
        """Stream 16-bit mono PCM (8000-16025 Hz) to his speaker: Rosie's own voice. Chunks of 512 samples, never more
        than half a second ahead of playback (the Vector SDK's limits)."""
        frame = 512 * 2
        secs = len(pcm) / 2 / rate
        finished = threading.Event()

        def requests():
            yield messages_pb2.ExternalAudioStreamRequest(audio_stream_prepare=messages_pb2.ExternalAudioStreamPrepare(
                audio_frame_rate=rate, audio_volume=volume))
            t0 = time.time()
            for i in range(0, len(pcm), frame):
                ahead = i / 2 / rate - (time.time() - t0)
                if ahead > 0.5:
                    time.sleep(ahead - 0.5)
                chunk = pcm[i:i + frame]
                yield messages_pb2.ExternalAudioStreamRequest(audio_stream_chunk=messages_pb2.ExternalAudioStreamChunk(
                    audio_chunk_size_bytes=len(chunk), audio_chunk_samples=chunk))
            yield messages_pb2.ExternalAudioStreamRequest(audio_stream_complete=messages_pb2.ExternalAudioStreamComplete())
            # Keep our side open until he says playback is complete: closing it ends his stream early, his audio starves,
            # and the next animation's sound crashed vic-anim (signal 11, WireOS 3.0.2.34d, 2026-10-21 19:46).
            finished.wait(max(0.0, secs - (time.time() - t0)) + 3.0)

        def run():
            t0 = time.time()
            try:
                for resp in self.stub.ExternalAudioStreamPlayback(requests(), timeout=secs + 15):
                    if resp.HasField("audio_stream_playback_complete") or resp.HasField("audio_stream_playback_failyer"):
                        return resp
            finally:
                finished.set()
                left = secs + 0.3 - (time.time() - t0)
                if left > 0:                                 # never hand control back while his speaker still plays
                    time.sleep(left)
        return self._speaking(run, ctl)

    # quiet when idle: his idle chirps and mumbles are animation sounds this firmware has no switch for, so he stays
    # muted and gets his voice back from the wake word (or a Rosie line) until shortly after he has answered.
    def set_volume(self, level: str):
        """level: MUTE, LOW, MEDIUM_LOW, MEDIUM, MEDIUM_HIGH or HIGH (his own settings document, like the app's slider).
        Sent as JSON: over gRPC MUTE is enum value 0, which protobuf leaves out, so "mute" arrived as "no change"
        (found on the robot 2026-10-21; WireOS's own settings page sends JSON too)."""
        self._update_settings({"master_volume": settings_pb2.Volume.Value(level)})

    def _update_settings(self, settings: dict):
        import httpx
        ctx = ssl.create_default_context(cadata=self._pem)
        ctx.check_hostname = False                          # his certificate names Vector-XXXX, not his address; the pin is what counts
        r = httpx.post(f"https://{self.ip}:{PORT}/v1/update_settings", json={"update_settings": True, "settings": settings},
                       headers={"Authorization": f"Bearer {self._guid}"}, verify=ctx, timeout=8)
        r.raise_for_status()

    def loud(self):
        if self.talk_volume:
            with self._vlock:
                if self._quiet:
                    self._quiet.cancel()
                    self._quiet = None
            self.set_volume(self.talk_volume)

    def quiet_after(self, secs: float):
        """Mute after `secs`, unless something speaks or a later answer pushes it back (the latest wins)."""
        if self.talk_volume:
            with self._vlock:
                if self._quiet:
                    self._quiet.cancel()
                self._quiet = threading.Timer(secs, self._mute)
                self._quiet.daemon = True
                self._quiet.start()

    def _mute(self):
        with self._vlock:
            if self._talking:                            # a Rosie line is being said: its own end mutes him
                return
            self._quiet = None
        try:
            self.set_volume("MUTE")
        except Exception:
            pass

    def eye_color(self, hue: float, saturation: float = 1.0):
        """His eye colour for now (not saved): it only shows while Rosie holds control, and his saved colour is back after."""
        self.stub.SetEyeColor(messages_pb2.SetEyeColorRequest(hue=hue, saturation=saturation), timeout=3)

    def save_eye_color(self, hue: float, saturation: float = 1.0):
        """His saved eye colour (custom_eye_color in his settings, the way WireOS's own settings page sets it). The SDK
        protos have no field for it, so this uses his gateway's JSON route with the same token and pinned certificate."""
        self._update_settings({"custom_eye_color": {"enabled": True, "hue": hue, "saturation": saturation}})

    # motors (need control: vector_pad.py holds him while you drive) ------------------------------------------------
    def drive(self, left_mmps: float, right_mmps: float, accel: float = 0.0):
        """Wheel speeds in mm/s (about 220 is his top speed); they keep going until the next call. accel (mm/s^2) ramps
        to the new speed instead of jumping (0 = at once)."""
        self.stub.DriveWheels(messages_pb2.DriveWheelsRequest(left_wheel_mmps=left_mmps, right_wheel_mmps=right_mmps,
                                                              left_wheel_mmps2=accel, right_wheel_mmps2=accel), timeout=3)

    def move_head(self, rad_per_s: float):
        """Tilt his head up (+) or down (-) until 0 or the end of its travel."""
        self.stub.MoveHead(messages_pb2.MoveHeadRequest(speed_rad_per_sec=rad_per_s), timeout=3)

    def move_lift(self, rad_per_s: float):
        """Raise (+) or lower (-) his lift until 0 or the end of its travel."""
        self.stub.MoveLift(messages_pb2.MoveLiftRequest(speed_rad_per_sec=rad_per_s), timeout=3)

    def stop_motors(self):
        self.stub.StopAllMotors(messages_pb2.StopAllMotorsRequest(), timeout=3)

    def animate(self, trigger: str, ctl="borrow"):
        """One of his own animations by trigger name (ListAnimationTriggers), with its sound."""
        req = messages_pb2.PlayAnimationTriggerRequest(animation_trigger=messages_pb2.AnimationTrigger(name=trigger), loops=1)
        return self._speaking(lambda: self.stub.PlayAnimationTrigger(req, timeout=30), ctl)

    def states(self):
        """His state about 15 times a second (RobotState: lift_height_mm, head_angle_rad, status bits) until the
        returned stream is cancelled."""
        return self.stub.EventStream(shared_pb2.EventRequest(white_list=shared_pb2.FilterList(list=["robot_state"])))

    def turn(self, degrees: float, speed_dps: float = 90.0):
        """Turn on the spot (positive = left) and wait until done."""
        import math
        self.stub.TurnInPlace(messages_pb2.TurnInPlaceRequest(angle_rad=math.radians(degrees), speed_rad_per_sec=math.radians(speed_dps),
                                                              accel_rad_per_sec2=math.radians(360), num_retries=1, id_tag=next(_tags)), timeout=15)

    def forward(self, mm: float, speed_mmps: float = 80.0):
        """Drive straight (negative = back) and wait until done; his cliff sensors still stop him."""
        self.stub.DriveStraight(messages_pb2.DriveStraightRequest(speed_mmps=speed_mmps, dist_mm=mm, num_retries=1, id_tag=next(_tags)), timeout=20)

    def drive_on_charger(self):
        """He finds his charger and backs onto it (his own docking), with control at DEFAULT so cliffs still win."""
        ctl = self.borrow(priority=behavior_pb2.ControlRequest.DEFAULT)
        try:
            self.stub.DriveOnCharger(messages_pb2.DriveOnChargerRequest(), timeout=90)
        finally:
            if ctl is not None:
                ctl.stop()

    def head_angle(self, degrees: float):
        """Point his head (-22 down to 45 up) and wait until done."""
        import math
        self.stub.SetHeadAngle(messages_pb2.SetHeadAngleRequest(angle_rad=math.radians(degrees), max_speed_rad_per_sec=3.0,
                                                                accel_rad_per_sec2=10.0, num_retries=1, id_tag=next(_tags)), timeout=8)

    def asleep(self) -> bool:
        """In power-save sleep (calm power mode): his camera is off. Unknown counts as awake."""
        try:
            call = self.states()
            for ev in call:
                if ev.event.WhichOneof("event_type") == "robot_state":
                    call.cancel()
                    return bool(ev.event.robot_state.status & CALM_POWER_MODE)
        except Exception:
            pass
        return False

    def picture(self, high_res=False) -> bytes:
        """One camera frame (JPEG bytes). Never asked for while he sleeps: on 2026-10-24 two requests made while a heat
        warning had him in emergency sleep (camera powered off) waited in his engine, were answered when he was picked
        up, and his engine crashed (SIGSEGV) restarting the camera four seconds later."""
        if self.asleep():
            raise CameraAsleep("his camera is off while he sleeps")
        r = self.stub.CaptureSingleImage(messages_pb2.CaptureSingleImageRequest(enable_high_resolution=high_res), timeout=6)
        return r.data

    def show(self, rgb565: bytes, duration_ms: int):
        """A picture on his face (needs control: vector_face.py draws only while he is held)."""
        self.stub.DisplayFaceImageRGB(messages_pb2.DisplayFaceImageRGBRequest(face_data=rgb565, duration_ms=duration_ms,
                                                                              interrupt_running=True), timeout=5)

    def version(self) -> str:
        """His firmware version, like 2.0.1.6076ep (ep = DDL's escape pod build)."""
        return self.stub.VersionState(messages_pb2.VersionStateRequest(), timeout=6).os_version

    def battery(self) -> dict:
        r = self.stub.BatteryState(messages_pb2.BatteryStateRequest(), timeout=5)
        return {"level": r.battery_level, "volts": round(r.battery_volts, 2), "charging": r.is_charging, "on_charger": r.is_on_charger_platform}

    def volume(self, level: str):
        """level: VOLUME_LOW, VOLUME_MEDIUM_LOW, VOLUME_MEDIUM, VOLUME_MEDIUM_HIGH or VOLUME_HIGH."""
        self.stub.SetMasterVolume(messages_pb2.MasterVolumeRequest(volume_level=messages_pb2.MasterVolumeLevel.Value(level)), timeout=5)


class CameraAsleep(RuntimeError):
    pass


class _Control:
    """One BehaviorControl stream: ask for control at a priority, keep the stream open while holding it.
    keep=True asks again whenever control is lost, until stop()."""

    def __init__(self, stub, priority, keep=False):
        self._stub = stub
        self._priority = priority
        self._keep = keep
        self._out: queue.Queue = queue.Queue()
        self.granted = threading.Event()
        self.lost = threading.Event()
        self.lost_at = time.time()                          # when control last went away (start counts: not granted yet)
        self._stopped = threading.Event()
        self._call = None

    def alive(self) -> bool:
        return not self._stopped.is_set() and (self.granted.is_set() or (self._keep and not self.lost.is_set()))

    def _requests(self):
        yield behavior_pb2.BehaviorControlRequest(control_request=behavior_pb2.ControlRequest(priority=self._priority))
        while True:
            item = self._out.get()
            if item is None:
                yield behavior_pb2.BehaviorControlRequest(control_release=behavior_pb2.ControlRelease())
                return
            yield item

    def _run(self):
        try:
            self._call = self._stub.BehaviorControl(self._requests())
            for resp in self._call:
                if resp.HasField("control_granted_response"):
                    self.granted.set()
                elif resp.HasField("control_lost_event") or resp.HasField("reserved_control_lost_event"):
                    self.granted.clear()
                    self.lost_at = time.time()
                    if self._keep and not self._stopped.is_set():
                        self._out.put(behavior_pb2.BehaviorControlRequest(control_request=behavior_pb2.ControlRequest(priority=self._priority)))
                    else:
                        self.lost.set()
        except (grpc.RpcError, ValueError):                 # ValueError: the channel was closed (Rosie's server stopping)
            pass
        finally:
            self.granted.clear()
            self.lost_at = time.time()                       # the stream ended: ensure_control asks again on a fresh one
            self.lost.set()

    def start(self, timeout: float) -> bool:
        threading.Thread(target=self._run, daemon=True, name="vector-control").start()
        return self.granted.wait(timeout)

    def stop(self):
        self._stopped.set()
        self._out.put(None)
        time.sleep(0.2)                                   # let the release reach Vector before the stream closes
        if self._call is not None:
            self._call.cancel()


def reachable(ip: str, port: int = PORT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=2):
            return True
    except OSError:
        return False
