"""외부 공유 주소를 알아내고, 접속 코드가 들어간 QR 코드를 만들고, 공개 주소가 실제로 열리는지 확인한다.

share.bat 이 호출한다:  python tools/share_info.py --code 123456
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAILSCALE = Path(r"C:\Program Files\Tailscale\tailscale.exe")
QR_PATH = ROOT / "share_qr.png"


def public_base_url() -> str:
    out = subprocess.run([str(TAILSCALE), "status", "--json"], capture_output=True, text=True, encoding="utf-8", check=True)
    dns = json.loads(out.stdout)["Self"]["DNSName"].rstrip(".")
    return f"https://{dns}"


def wait_until_reachable(url: str, timeout_s: int = 90) -> bool:
    """Funnel 은 처음 켤 때 HTTPS 인증서를 받느라 수십 초 걸릴 수 있다."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(3)
    return False


def write_qr(url: str, path: Path) -> None:
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    base = public_base_url()
    with_code = f"{base}/?code={args.code}"
    write_qr(with_code, QR_PATH)

    print()
    print("=" * 64)
    print(f"  공개 주소   : {base}")
    print(f"  접속 코드   : {args.code}")
    print(f"  바로 들어가는 링크 (QR 과 같음): {with_code}")
    print(f"  QR 이미지   : {QR_PATH}")
    print("=" * 64)
    print("공개 주소가 열리는지 확인하는 중... (처음엔 인증서 발급으로 최대 1분 걸릴 수 있음)")
    ok = wait_until_reachable(f"{base}/api/status")
    print("  -> 외부에서 접속 가능합니다." if ok else "  -> 아직 응답이 없습니다. 잠시 뒤 휴대폰(LTE)으로 직접 열어 보세요.")
    if not args.no_open:
        webbrowser.open(str(QR_PATH))
        webbrowser.open(with_code)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
