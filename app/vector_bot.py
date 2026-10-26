# board #790 safety-trust-first-certificate
import queue
import socket
import ssl
import threading
import time

import grpc

from vector_pb.sdk import behavior_pb2, external_interface_pb2_grpc, messages_pb2, settings_pb2, shared_pb2

PORT = 443
CONNECT_S = 6
# board #790 why-sit-default-priority
SIT_PRIORITY = behavior_pb2.ControlRequest.DEFAULT
MENU_PRIORITY = SIT_PRIORITY
DRIVE_PRIORITY = SIT_PRIORITY
FIRST_SDK_TAG, LAST_SDK_TAG = 2000001, 3000000
_tags = iter(range(FIRST_SDK_TAG, LAST_SDK_TAG))
CALM_POWER_MODE = 0x400
BUTTON_PRESSED = 0x10


def fetch_cert(ip: str, port: int = PORT) -> str:
    return ssl.get_server_certificate((ip, port), timeout=CONNECT_S)


def cert_name(pem: str) -> str:
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
            grpc.access_token_call_credentials(guid))
        opts = [("grpc.ssl_target_name_override", self.name)] if self.name else []
        self.channel = grpc.secure_channel(f"{ip}:{PORT}", creds, options=opts)
        self.stub = external_interface_pb2_grpc.ExternalInterfaceStub(self.channel)
        self._hold: _Control | None = None
        self._priority = SIT_PRIORITY
        self._want_hold = False
        self._lock = threading.Lock()
        self.talk_volume: str | None = None
        self._quiet: threading.Timer | None = None
        self._vlock = threading.Lock()
        self._talking = 0

    def close(self, restore_volume=True):
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

    @property
    def held(self) -> bool:
        return bool(self._want_hold and self._hold is not None)

    def hold(self, timeout=8.0, priority=SIT_PRIORITY) -> bool:
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
            self._hold.stop()
            self._hold = None
            return False

    def has_control(self) -> bool:
        hold = self._hold
        return bool(hold is not None and hold.granted.is_set())

    def ensure_control(self, after_s: float = 3.0, timeout: float = 5.0) -> bool:
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

    def borrow(self, timeout=8.0, priority=behavior_pb2.ControlRequest.OVERRIDE_BEHAVIORS):
        hold = self._hold
        if hold is not None and hold.alive():
            hold.granted.wait(timeout)
            return None
        ctl = _Control(self.stub, priority)
        if not ctl.start(timeout):
            ctl.stop()
            raise TimeoutError("Vector did not hand over control")
        return ctl

    def _speaking(self, fn, ctl="borrow"):
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
        req = messages_pb2.SayTextRequest(text=text, use_vector_voice=True, duration_scalar=1.0)
        return self._speaking(lambda: self.stub.SayText(req, timeout=timeout), ctl)

    def play(self, pcm: bytes, rate: int = 16000, volume: int = 100, ctl="borrow"):
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
                if left > 0:
                    time.sleep(left)
        return self._speaking(run, ctl)

    def set_volume(self, level: str):
        # board #790 quirk-mute-sent-as-json
        self._update_settings({"master_volume": settings_pb2.Volume.Value(level)})

    def _update_settings(self, settings: dict):
        import httpx
        ctx = ssl.create_default_context(cadata=self._pem)
        ctx.check_hostname = False
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
        if self.talk_volume:
            with self._vlock:
                if self._quiet:
                    self._quiet.cancel()
                self._quiet = threading.Timer(secs, self._mute)
                self._quiet.daemon = True
                self._quiet.start()

    def _mute(self):
        with self._vlock:
            if self._talking:
                return
            self._quiet = None
        try:
            self.set_volume("MUTE")
        except Exception:
            pass

    def eye_color(self, hue: float, saturation: float = 1.0):
        self.stub.SetEyeColor(messages_pb2.SetEyeColorRequest(hue=hue, saturation=saturation), timeout=3)

    def save_eye_color(self, hue: float, saturation: float = 1.0):
        self._update_settings({"custom_eye_color": {"enabled": True, "hue": hue, "saturation": saturation}})

    def drive(self, left_mmps: float, right_mmps: float, accel: float = 0.0):
        self.stub.DriveWheels(messages_pb2.DriveWheelsRequest(left_wheel_mmps=left_mmps, right_wheel_mmps=right_mmps,
                                                              left_wheel_mmps2=accel, right_wheel_mmps2=accel), timeout=3)

    def move_head(self, rad_per_s: float):
        self.stub.MoveHead(messages_pb2.MoveHeadRequest(speed_rad_per_sec=rad_per_s), timeout=3)

    def move_lift(self, rad_per_s: float):
        self.stub.MoveLift(messages_pb2.MoveLiftRequest(speed_rad_per_sec=rad_per_s), timeout=3)

    def stop_motors(self):
        self.stub.StopAllMotors(messages_pb2.StopAllMotorsRequest(), timeout=3)

    def animate(self, trigger: str, ctl="borrow"):
        req = messages_pb2.PlayAnimationTriggerRequest(animation_trigger=messages_pb2.AnimationTrigger(name=trigger), loops=1)
        return self._speaking(lambda: self.stub.PlayAnimationTrigger(req, timeout=30), ctl)

    def states(self):
        return self.stub.EventStream(shared_pb2.EventRequest(white_list=shared_pb2.FilterList(list=["robot_state"])))

    def turn(self, degrees: float, speed_dps: float = 90.0):
        import math
        self.stub.TurnInPlace(messages_pb2.TurnInPlaceRequest(angle_rad=math.radians(degrees), speed_rad_per_sec=math.radians(speed_dps),
                                                              accel_rad_per_sec2=math.radians(360), num_retries=1, id_tag=next(_tags)), timeout=15)

    def forward(self, mm: float, speed_mmps: float = 80.0):
        self.stub.DriveStraight(messages_pb2.DriveStraightRequest(speed_mmps=speed_mmps, dist_mm=mm, num_retries=1, id_tag=next(_tags)), timeout=20)

    def drive_on_charger(self):
        ctl = self.borrow(priority=behavior_pb2.ControlRequest.DEFAULT)
        try:
            self.stub.DriveOnCharger(messages_pb2.DriveOnChargerRequest(), timeout=90)
        finally:
            if ctl is not None:
                ctl.stop()

    def head_angle(self, degrees: float):
        import math
        self.stub.SetHeadAngle(messages_pb2.SetHeadAngleRequest(angle_rad=math.radians(degrees), max_speed_rad_per_sec=3.0,
                                                                accel_rad_per_sec2=10.0, num_retries=1, id_tag=next(_tags)), timeout=8)

    def asleep(self) -> bool:
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
        # board #790 safety-no-camera-while-asleep
        if self.asleep():
            raise CameraAsleep("his camera is off while he sleeps")
        r = self.stub.CaptureSingleImage(messages_pb2.CaptureSingleImageRequest(enable_high_resolution=high_res), timeout=6)
        return r.data

    def show(self, rgb565: bytes, duration_ms: int):
        self.stub.DisplayFaceImageRGB(messages_pb2.DisplayFaceImageRGBRequest(face_data=rgb565, duration_ms=duration_ms,
                                                                              interrupt_running=True), timeout=5)

    def version(self) -> str:
        return self.stub.VersionState(messages_pb2.VersionStateRequest(), timeout=6).os_version

    def battery(self) -> dict:
        r = self.stub.BatteryState(messages_pb2.BatteryStateRequest(), timeout=5)
        return {"level": r.battery_level, "volts": round(r.battery_volts, 2), "charging": r.is_charging, "on_charger": r.is_on_charger_platform}

    def volume(self, level: str):
        self.stub.SetMasterVolume(messages_pb2.MasterVolumeRequest(volume_level=messages_pb2.MasterVolumeLevel.Value(level)), timeout=5)


class CameraAsleep(RuntimeError):
    pass


class _Control:

    def __init__(self, stub, priority, keep=False):
        self._stub = stub
        self._priority = priority
        self._keep = keep
        self._out: queue.Queue = queue.Queue()
        self.granted = threading.Event()
        self.lost = threading.Event()
        self.lost_at = time.time()
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
        except (grpc.RpcError, ValueError):
            pass
        finally:
            self.granted.clear()
            self.lost_at = time.time()
            self.lost.set()

    def start(self, timeout: float) -> bool:
        threading.Thread(target=self._run, daemon=True, name="vector-control").start()
        return self.granted.wait(timeout)

    def stop(self):
        self._stopped.set()
        self._out.put(None)
        time.sleep(0.2)
        if self._call is not None:
            self._call.cancel()


def reachable(ip: str, port: int = PORT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=2):
            return True
    except OSError:
        return False
