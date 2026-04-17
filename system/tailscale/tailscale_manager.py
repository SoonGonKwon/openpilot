import json
import os
import platform
import subprocess
import tarfile
import tempfile
import time
import urllib.request

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

TAILSCALE_BIN = "/data/tailscale/tailscale"
TAILSCALE_DAEMON = "/data/tailscale/tailscaled"
TAILSCALE_SOCKET = "/tmp/tailscaled.sock"
TAILSCALE_DOWNLOAD_URL = "https://pkgs.tailscale.com/stable/tailscale_latest_arm64.tgz"


def install_tailscale() -> bool:
  """Tailscale 바이너리가 없으면 자동 다운로드 및 설치"""
  if os.path.isfile(TAILSCALE_BIN):
    return True

  cloudlog.info("Tailscale binary not found, installing...")
  try:
    os.makedirs("/data/tailscale", exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as f:
      tmp_path = f.name

    cloudlog.info(f"Downloading tailscale from {TAILSCALE_DOWNLOAD_URL}")
    req = urllib.request.urlopen(TAILSCALE_DOWNLOAD_URL, timeout=30)
    with open(tmp_path, 'wb') as f:
      f.write(req.read())

    with tarfile.open(tmp_path, "r:gz") as tar:
      for member in tar.getmembers():
        if member.name.endswith("/tailscale") or member.name.endswith("/tailscaled"):
          member.name = os.path.basename(member.name)
          tar.extract(member, "/data/tailscale")

    os.chmod(TAILSCALE_BIN, 0o755)
    if os.path.isfile(TAILSCALE_DAEMON):
      os.chmod(TAILSCALE_DAEMON, 0o755)
    os.unlink(tmp_path)
    cloudlog.info("Tailscale installed successfully")
    return True
  except Exception as e:
    cloudlog.error(f"Tailscale install failed: {e}")
    return False

NetworkType = log.DeviceState.NetworkType
_daemon_proc: subprocess.Popen | None = None


def ensure_daemon_running() -> bool:
  """tailscaled 데몬이 실행 중인지 확인하고 없으면 시작"""
  global _daemon_proc

  # 소켓이 있으면 이미 실행 중
  if os.path.exists(TAILSCALE_SOCKET):
    return True

  if not os.path.isfile(TAILSCALE_DAEMON):
    cloudlog.warning("tailscaled binary not found")
    return False

  try:
    cloudlog.info("Starting tailscaled daemon...")
    os.makedirs("/data/tailscale/state", exist_ok=True)
    _daemon_proc = subprocess.Popen(
      ["sudo", TAILSCALE_DAEMON, f"--socket={TAILSCALE_SOCKET}", "--state=/data/tailscale/state/tailscaled.state"],
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # 데몬 시작 대기
    return os.path.exists(TAILSCALE_SOCKET)
  except Exception as e:
    cloudlog.error(f"Failed to start tailscaled: {e}")
    return False


def run_tailscale(*args: str) -> subprocess.CompletedProcess[bytes]:
  try:
    return subprocess.run(
      ["sudo", TAILSCALE_BIN, f"--socket={TAILSCALE_SOCKET}", *args],
      check=False,
      capture_output=True,
      timeout=15,
    )
  except subprocess.TimeoutExpired:
    cloudlog.warning(f"tailscale {args} timed out")
    return subprocess.CompletedProcess(args, returncode=1, stdout=b"", stderr=b"timeout")


def get_backend_state() -> tuple[str, bool]:
  proc = run_tailscale("status", "--json")
  if proc.returncode != 0:
    stderr = proc.stderr.decode("utf-8", errors="ignore").strip()
    cloudlog.warning(f"tailscale status failed: {stderr}")
    return "Stopped", False

  try:
    status = json.loads(proc.stdout)
  except json.JSONDecodeError:
    cloudlog.exception("failed to decode tailscale status json")
    return "Stopped", False

  backend_state = status.get("BackendState", "Stopped")
  return backend_state, backend_state == "Running"


def publish_backend_state(params: Params) -> bool:
  backend_state, running = get_backend_state()
  params.put("TailscaleBackendState", backend_state)
  params.put_bool("TailscaleBackendRunning", running)
  if running:
    ip_proc = run_tailscale("ip", "--4")
    if ip_proc.returncode == 0:
      params.put("TailscaleIP", ip_proc.stdout.decode().strip())
    else:
      params.put("TailscaleIP", "")
  return running


def reconcile_state(params: Params, enabled: bool) -> None:
  cmd = "up" if enabled else "down"
  proc = run_tailscale(cmd)
  if proc.returncode != 0:
    stderr = proc.stderr.decode("utf-8", errors="ignore").strip()
    cloudlog.warning(f"tailscale {cmd} failed: {stderr}")

  publish_backend_state(params)


def main() -> None:
  params = Params()

  if not install_tailscale():
    cloudlog.error("Tailscale not available, exiting tailscale_manager")
    while True:
      time.sleep(3600)

  ensure_daemon_running()
  running = publish_backend_state(params)
  if params.get("TailscaleEnabled") is None:
    params.put_bool("TailscaleEnabled", running)

  cloudlog.info("tailscale_manager: daemon started, no further polling needed")
  # tailscaled가 연결 상태를 자체 유지 — 폴링 불필요
  while True:
    time.sleep(3600)


if __name__ == "__main__":
  main()
