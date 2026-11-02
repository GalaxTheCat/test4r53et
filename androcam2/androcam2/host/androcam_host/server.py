"""Phone-facing network side: TCP session server + UDP discovery responder."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Callable, Dict, List, Optional

from . import protocol as P


class Session:
    def __init__(self, sock: socket.socket, addr):
        self.sock, self.addr = sock, addr
        self.info: dict = {}
        self.status: dict = {}
        self.since = time.time()
        self.send_lock = threading.Lock()

    def send(self, data: bytes) -> bool:
        try:
            with self.send_lock:
                self.sock.sendall(data)
            return True
        except OSError:
            return False

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class PhoneServer:
    FAIL_LIMIT, FAIL_WINDOW, BLOCK_SECONDS = 5, 60, 60

    def __init__(self, get_code: Callable[[], str],
                 on_video: Callable[[dict, bytes], None],
                 on_audio: Callable[[dict, bytes], None],
                 on_change: Callable[[], None],
                 pc_name: Optional[str] = None, host: str = "0.0.0.0",
                 port: int = P.PORT_PHONE, discovery_port: int = P.PORT_DISCOVERY):
        self.get_code, self.on_video, self.on_audio, self.on_change = get_code, on_video, on_audio, on_change
        self.pc_name = pc_name or socket.gethostname()
        self.host, self.port, self.discovery_port = host, port, discovery_port
        self.session: Optional[Session] = None
        self._lock = threading.Lock()
        self._stop = False
        self._fails: Dict[str, List[float]] = {}
        self._blocked: Dict[str, float] = {}
        self._tcp: Optional[socket.socket] = None
        self._udp: Optional[socket.socket] = None
        self.auth_failures = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp.bind((self.host, self.port))
        self.port = self._tcp.getsockname()[1]
        self._tcp.listen(4)
        threading.Thread(target=self._accept_loop, daemon=True, name="tcp-accept").start()
        try:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp.bind((self.host, self.discovery_port))
            self.discovery_port = self._udp.getsockname()[1]
            threading.Thread(target=self._discovery_loop, daemon=True, name="udp-discovery").start()
        except OSError:
            self._udp = None            # discovery is a convenience; manual IP still works

    def stop(self) -> None:
        self._stop = True
        for s in (self._tcp, self._udp):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        with self._lock:
            if self.session:
                self.session.close()

    # ---------------------------------------------------------------- public helpers
    @property
    def connected(self) -> bool:
        return self.session is not None

    def send_control(self, obj: dict) -> bool:
        s = self.session
        return bool(s and s.send(P.pack_json(P.T_CONTROL, obj)))

    # ---------------------------------------------------------------- discovery
    def _discovery_loop(self) -> None:
        while not self._stop and self._udp:
            try:
                data, addr = self._udp.recvfrom(256)
            except OSError:
                return
            if data.strip() == b"ANDROCAM?":
                reply = json.dumps({"app": "androcam", "name": self.pc_name, "port": self.port,
                                    "ver": P.PROTO_VERSION}).encode()
                try:
                    self._udp.sendto(reply, addr)
                except OSError:
                    pass

    # ---------------------------------------------------------------- sessions
    def _accept_loop(self) -> None:
        while not self._stop and self._tcp:
            try:
                sock, addr = self._tcp.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(sock, addr), daemon=True, name="phone").start()

    def _is_blocked(self, ip: str) -> bool:
        until = self._blocked.get(ip, 0)
        return time.time() < until

    def _note_failure(self, ip: str) -> None:
        now = time.time()
        recent = [t for t in self._fails.get(ip, []) if now - t < self.FAIL_WINDOW] + [now]
        self._fails[ip] = recent
        self.auth_failures += 1
        if len(recent) >= self.FAIL_LIMIT:
            self._blocked[ip] = now + self.BLOCK_SECONDS
            self._fails[ip] = []

    def _handle(self, sock: socket.socket, addr) -> None:
        ip = addr[0]
        sess = Session(sock, addr)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if self._is_blocked(ip):
                sess.send(P.pack_json(P.T_AUTH_FAIL, {"reason": "too many attempts, wait a minute"}))
                return
            sock.settimeout(8)
            nonce = os.urandom(16)
            sess.send(P.pack(P.T_CHALLENGE, nonce))
            ptype, _, payload = P.read_packet(sock)
            if ptype != P.T_AUTH or not P.verify_auth(self.get_code(), nonce, payload):
                self._note_failure(ip)
                time.sleep(0.4)                           # slow down guessing
                sess.send(P.pack_json(P.T_AUTH_FAIL, {"reason": "wrong pairing code"}))
                return
            with self._lock:                              # register BEFORE telling the phone it is in,
                old, self.session = self.session, sess    # so any command that follows finds the session
            if old:
                old.close()
            sess.send(P.pack_json(P.T_AUTH_OK, {"pc": self.pc_name, "ver": P.PROTO_VERSION}))
            sock.settimeout(15)
            self.on_change()
            self._loop(sess)
        except (ConnectionError, socket.timeout, P.ProtocolError, OSError):
            pass
        finally:
            with self._lock:
                was_current = self.session is sess
                if was_current:
                    self.session = None
            sess.close()
            if was_current:
                self.on_change()

    def _loop(self, sess: Session) -> None:
        while not self._stop:
            ptype, _, payload = P.read_packet(sess.sock)
            if ptype == P.T_VIDEO:
                meta, jpeg = P.unpack_video(payload)
                self.on_video(meta, jpeg)
            elif ptype == P.T_AUDIO:
                meta, pcm = P.unpack_audio(payload)
                self.on_audio(meta, pcm)
            elif ptype == P.T_HELLO:
                sess.info = P.parse_json(payload) or {}
                self.on_change()
            elif ptype == P.T_STATUS:
                sess.status = P.parse_json(payload) or {}
                self.on_change()
            elif ptype == P.T_PING:
                sess.send(P.pack(P.T_PONG))
