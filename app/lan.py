"""ลิงก์เข้าระบบจากมือถือแบบง่าย (ไม่ต้องจำ IP)

- ประกาศชื่อเครื่องในวง Wi-Fi ด้วย mDNS: http://icstorage.local  (มือถือ/คอมในวงเดียวกันพิมพ์ชื่อนี้ได้เลย)
- เปิดที่พอร์ต 80 (ไม่ต้องพิมพ์ :8800) และให้พอร์ต 8800 เดิมพาไปที่อยู่ใหม่ (ลิงก์/บุ๊กมาร์กเก่ายังใช้ได้)
- IP เครื่องเปลี่ยน (DHCP) -> ประกาศ IP ใหม่ให้เองทุก 30 วินาที
เปิดใช้เมื่อสั่งรันด้วย start_ic_storage.bat (ตั้ง IC_PORT) — ตอนทดสอบไม่ประกาศชื่อ
"""
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get("IC_HOSTNAME", "icstorage")
PORT = int(os.environ.get("IC_PORT", "0") or 0)        # 0 = ไม่ได้รันผ่านตัวเปิดโปรแกรม (เช่น ทดสอบ)
OLD_PORT = 8800

_stop = threading.Event()
_state: dict = {"ip": "", "mdns": False, "redirect": False}


def lan_ip() -> str:
    """IP ของเครื่องในวง LAN (ไม่ส่งข้อมูลจริง แค่ถามเส้นทาง)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _port() -> int:
    return PORT or OLD_PORT


def _fmt(host: str) -> str:
    return f"http://{host}" + ("" if _port() == 80 else f":{_port()}")


def urls() -> dict:
    ip = _state["ip"] or lan_ip()
    return {"name": _fmt(f"{NAME}.local"), "ip": _fmt(ip), "mdns": _state["mdns"], "port": _port()}


# ---------------- mDNS ----------------

def _mdns_loop():
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        print("lan: ไม่มี zeroconf — ใช้ลิงก์ IP แทน (pip install zeroconf)")
        return
    zc, info = None, None
    try:
        zc = Zeroconf()
        while not _stop.is_set():
            ip = lan_ip()
            if ip != _state["ip"] or info is None:
                new = ServiceInfo("_http._tcp.local.", f"IC Storage ({NAME})._http._tcp.local.",
                                  addresses=[socket.inet_aton(ip)], port=_port(), server=f"{NAME}.local.",
                                  properties={"path": "/"})
                if info is None:
                    zc.register_service(new, allow_name_change=True)
                else:
                    zc.update_service(new)
                info, _state["ip"], _state["mdns"] = new, ip, True
                print(f"lan: ประกาศชื่อ {NAME}.local -> {ip}", flush=True)
            _stop.wait(30)
    except Exception as e:   # mDNS ใช้ไม่ได้ (เช่น ไฟร์วอลล์) -> ยังเข้าด้วย IP ได้
        _state["mdns"] = False
        print("lan: mDNS ใช้ไม่ได้:", repr(e), flush=True)
    finally:
        if zc:
            try:
                if info:
                    zc.unregister_service(info)
                zc.close()
            except Exception:
                pass


# ---------------- พอร์ตเดิม 8800 -> ที่อยู่ใหม่ ----------------

class _Redirect(BaseHTTPRequestHandler):
    def do_GET(self):
        host = (self.headers.get("Host") or lan_ip()).split(":")[0]
        self.send_response(301)
        self.send_header("Location", f"http://{host}" + ("" if _port() == 80 else f":{_port()}") + self.path)
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


def _redirect_loop():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", OLD_PORT), _Redirect)
    except OSError as e:
        print(f"lan: เปิดพอร์ต {OLD_PORT} เพื่อพาไปที่อยู่ใหม่ไม่ได้:", e)
        return
    _state["redirect"] = True
    srv.timeout = 0.5
    while not _stop.is_set():
        srv.handle_request()
    srv.server_close()


def start():
    if not PORT:
        return
    _state["ip"] = lan_ip()
    threading.Thread(target=_mdns_loop, daemon=True, name="ic-mdns").start()
    if PORT != OLD_PORT:
        threading.Thread(target=_redirect_loop, daemon=True, name="ic-redirect").start()


def stop():
    _stop.set()
