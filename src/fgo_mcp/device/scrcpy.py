import random
import socket
import time
import struct
import threading
import subprocess

import av
import cv2
import numpy as np

from fgo_mcp.device.adb import ADBError, adb
from fgo_mcp.path import ROOT

VERSION = "5.0.1"
SERVER_JAR = ROOT / "vendor" / f"scrcpy-server-v{VERSION}"
DEVICE_JAR = "/data/local/tmp/fgo-scrcpy-server.jar"

SESSION_FLAG = 1 << 63  # packet header flags of the video stream
CONFIG_FLAG = 1 << 62
TOUCH = 2  # control message type
DOWN, UP, MOVE = 0, 1, 2
FINGER = -2

def read_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n-len(data))
        if not chunk:
            raise ConnectionError("scrcpy socket closed")
        data += chunk
    return data

class ScrcpyStream:
    def __init__(self, max_size: int = 1472):
        self.max_size = max_size
        self.size: tuple[int, int] | None = None
        self.frame = None   #latest decoded frame
        self.frames = 0     #number of decode frames
        self.error = None
        self.new_frame = threading.Condition()
        self.send_lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.server = self.video = self.control = self.port = self.thread = None

    def start(self) -> None:
        with self.start_lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop()
            self.error = None
            adb("push", str(SERVER_JAR), str(DEVICE_JAR))
            scid = f"{random.getrandbits(31):08x}"
            self.port = int(adb("forward", "tcp:0", f"localabstract:scrcpy_{scid}"))
            server_args = [
                f"CLASSPATH={DEVICE_JAR}", "app_process", "/", "com.genymobile.scrcpy.Server", VERSION,
                f"scid={scid}", "log_level=warn", "tunnel_forward=true", "audio=false", "control=true",
                "video_codec=h264", f"max_size={self.max_size}", "max_fps=30", "video_bit_rate=8000000",
                "stay_awake=true", "cleanup=true"
            ]
            self.server = subprocess.Popen(["adb", "shell", *server_args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.video = self.connect_video()
            self.control = socket.create_connection(("127.0.0.1", self.port))
            read_exact(self.video, 64)
            self.thread = threading.Thread(target = self.decode_video, daemon=True)
            self.thread.start()
            threading.Thread(target=self.ignore_device_messages, daemon=True).start()
            self.wait_for_frame()

    def connect_video(self) -> socket.socket:
        for _ in range(100):
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                if sock.recv(1):
                    sock.settimeout(None)
                    return sock
                sock.close()
            except OSError as e:
                pass
            time.sleep(0.1)
        raise ADBError("the scrcpy server did not start")

    def stop(self) -> None:
        for sock in (self.video, self.control):
            if sock:
                sock.close()
        if self.server:
            self.server.terminate()
        if self.port:
            try:
                adb("forward", "--remove", f"tcp:{self.port}")
            except ADBError:
                pass
        self.server = self.port = self.video = self.control = self.thread = None

    def decode_video(self) -> None:
        try:
            read_exact(self.video, 4)
            decoder = av.CodecContext.create("h264", "r")
            config = b""
            while True:
                headers = read_exact(self.video, 12)
                pts = struct.unpack(">Q", headers[:8])[0]
                if pts & SESSION_FLAG:  #check for new session
                    self.size = struct.unpack(">II", headers[4:12])
                    continue
                packet = read_exact(self.video, struct.unpack(">I", headers[8:12])[0])
                if pts & CONFIG_FLAG:
                    config = packet
                    continue
                for frame in decoder.decode(av.Packet(config + packet)):
                    with self.new_frame:
                        self.frame = frame
                        self.frames += 1
                        self.new_frame.notify_all()
                config = b""
        except Exception as e:
            with self.new_frame:
                self.error = e
                self.new_frame.notify_all()

    def ignore_device_messages(self) -> None:
        try:
            while self.control and self.control.recv(4096):
                pass
        except OSError as e:
            pass

    def wait_for_frame(self, timeout = 10) -> None:
        with self.new_frame:
            self.new_frame.wait_for(lambda: self.frame is not None or self.error, timeout=timeout)
        if self.error or self.frame is None:
            raise ADBError(f"no video from the device ({self.error or 'timeout'})")

    def latest(self) -> np.ndarray:
        self.wait_for_frame()
        try:
            return self.frame.to_ndarray(format="bgr24")
        except OSError as e:    # some phones send colour metadata that PyAV refuses: convert with OpenCV instead
            return cv2.cvtColor(self.frame.to_ndarray(format="yuv420p"), cv2.COLOR_YUV2BGR_I420)

    def _touch(self, action: int, x: int, y: int) -> None:
        pressure = 0 if action==UP else 0xFFFF
        message = struct.pack(">BBqiiHHHii", TOUCH, action, FINGER, x, y, *self.size, pressure, 0, 0)
        with self.send_lock:
            self.control.sendall(message)

    def tap(self, x: int, y: int) -> None:
        self._touch(DOWN, x, y)
        time.sleep(0.05)
        self._touch(UP, x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        steps = max(duration_ms//16, 2)
        self._touch(DOWN, x1, y1)
        for i in range(1, steps+1):
            t = i/steps
            self._touch(MOVE, round((x1 + (x2 - x1)*t)), round((y1 + (y2 - y1)*t)))
            time.sleep(duration_ms/1000/steps)
        self._touch(UP, x2, y2)

