#!/usr/bin/env python3
"""
주행 로그 자동 압축 및 업로드
- params: LogAutoUpload (bool, default off)
- params: LogUploadServer (string, default "http://10.21.1.57:8080")
- 주행 종료 후 최신 세그먼트 디렉토리 감지
- rlog, qlog를 zst 압축 (없으면 스킵)
- HTTP POST로 서버에 업로드
- 업로드 완료 세그먼트는 마킹 (재업로드 방지)
"""

import os
import io
import time
import subprocess
import tempfile
from pathlib import Path

import requests

try:
  from openpilot.common.params import Params
except ImportError:
  Params = None

try:
  from openpilot.system.swaglog import cloudlog
except ImportError:
  import logging
  cloudlog = logging.getLogger("log_uploader")
  cloudlog.addHandler(logging.StreamHandler())
  cloudlog.setLevel(logging.INFO)

REALDATA_DIR = "/data/media/0/realdata"
DEFAULT_SERVER = "https://logs.relena.online"
UPLOAD_MARKER = ".uploaded"
LOG_FILES = ["rlog", "qlog"]
POLL_INTERVAL = 60  # seconds between checks when disabled or idle


def compress_zstd(src_path: Path) -> bytes | None:
  """
  src_path 파일을 zstd 압축하여 bytes 반환.
  zstandard 라이브러리가 있으면 사용, 없으면 subprocess zstd 사용.
  실패 시 None 반환.
  """
  try:
    import zstandard as zstd
    cctx = zstd.ZstdCompressor(level=3)
    data = src_path.read_bytes()
    return cctx.compress(data)
  except ImportError:
    pass

  # fallback: subprocess zstd
  try:
    result = subprocess.run(
      ["zstd", "-3", "-c", str(src_path)],
      capture_output=True,
      timeout=120,
    )
    if result.returncode == 0:
      return result.stdout
    cloudlog.warning(f"log_uploader: zstd subprocess failed for {src_path}: {result.stderr.decode()}")
    return None
  except (FileNotFoundError, subprocess.TimeoutExpired) as e:
    cloudlog.warning(f"log_uploader: zstd not available or timed out for {src_path}: {e}")
    return None


def get_pending_segments(realdata_dir: Path) -> list[Path]:
  """
  아직 업로드되지 않은 세그먼트 디렉토리 목록 반환 (오래된 것부터).
  마커 파일(.uploaded)이 없는 디렉토리가 대상.
  """
  if not realdata_dir.exists():
    return []

  segments = []
  for route_dir in sorted(realdata_dir.iterdir()):
    if not route_dir.is_dir():
      continue
    for seg_dir in sorted(route_dir.iterdir()):
      if not seg_dir.is_dir():
        continue
      marker = seg_dir / UPLOAD_MARKER
      if not marker.exists():
        segments.append(seg_dir)

  return segments


def upload_segment(seg_dir: Path, server_url: str) -> bool:
  """
  세그먼트 디렉토리의 로그 파일을 압축 후 HTTP POST로 업로드.
  성공 시 True, 실패 시 False 반환.
  """
  upload_url = server_url.rstrip("/") + "/api/log/upload"
  files_uploaded = 0
  files_attempted = 0

  for log_name in LOG_FILES:
    # 원본 파일 또는 이미 압축된 .zst 파일 탐색
    src_path = seg_dir / log_name
    zst_path = seg_dir / (log_name + ".zst")

    compressed_data: bytes | None = None
    upload_filename: str

    if zst_path.exists():
      # 이미 압축된 파일 사용
      try:
        compressed_data = zst_path.read_bytes()
        upload_filename = zst_path.name
      except OSError as e:
        cloudlog.warning(f"log_uploader: failed to read {zst_path}: {e}")
        continue
    elif src_path.exists():
      # 원본 파일 압축
      compressed_data = compress_zstd(src_path)
      if compressed_data is None:
        cloudlog.warning(f"log_uploader: compression failed for {src_path}, skipping")
        continue
      upload_filename = log_name + ".zst"
    else:
      # 해당 로그 파일 없음, 스킵
      continue

    files_attempted += 1
    route_name = seg_dir.parent.name
    seg_name = seg_dir.name
    remote_path = f"{route_name}/{seg_name}/{upload_filename}"

    try:
      resp = requests.post(
        upload_url,
        files={"file": (upload_filename, io.BytesIO(compressed_data), "application/octet-stream")},
        data={"route": route_name, "segment": seg_name},
        timeout=120,
      )
      if resp.status_code == 200:
        files_uploaded += 1
        cloudlog.info(f"log_uploader: uploaded {remote_path}")
      else:
        cloudlog.warning(f"log_uploader: server returned {resp.status_code} for {remote_path}: {resp.text[:200]}")
    except requests.RequestException as e:
      cloudlog.warning(f"log_uploader: upload failed for {remote_path}: {e}")

  if files_attempted == 0:
    # 로그 파일이 하나도 없는 세그먼트 — 마커만 생성하고 성공 처리
    cloudlog.info(f"log_uploader: no log files in {seg_dir}, marking as uploaded")
    return True

  if files_uploaded == files_attempted:
    return True

  cloudlog.warning(f"log_uploader: {files_uploaded}/{files_attempted} files uploaded for {seg_dir}")
  return False


def mark_uploaded(seg_dir: Path) -> None:
  marker = seg_dir / UPLOAD_MARKER
  try:
    marker.touch()
  except OSError as e:
    cloudlog.warning(f"log_uploader: failed to create marker {marker}: {e}")


def main() -> None:
  if Params is None:
    cloudlog.error("log_uploader: Params unavailable, idling forever")
    while True:
      time.sleep(POLL_INTERVAL)
  params = Params()
  realdata_dir = Path(REALDATA_DIR)

  # 첫 실행시 1회 강제 활성화 (마커 파일로 중복 방지).
  # params_keys.h의 default가 "0"이라 None 체크는 의미 없음.
  init_marker = Path("/data/params/d/.log_uploader_initialized")
  if not init_marker.exists():
    try:
      params.put_bool("LogAutoUpload", True)
      init_marker.touch()
      cloudlog.info("log_uploader: auto-enabled on first run (marker created)")
    except Exception as e:
      cloudlog.warning(f"log_uploader: failed to set default param: {e}")

  cloudlog.info("log_uploader: started")

  while True:
    try:
      if not params.get_bool("LogAutoUpload"):
        time.sleep(POLL_INTERVAL)
        continue

      server_raw = params.get("LogUploadServer", encoding="utf-8")
      server_url = (server_raw or "").strip() or DEFAULT_SERVER

      segments = get_pending_segments(realdata_dir)
      if not segments:
        time.sleep(POLL_INTERVAL)
        continue

      cloudlog.info(f"log_uploader: {len(segments)} segment(s) pending upload to {server_url}")

      for seg_dir in segments:
        # 재확인: 업로드 중 설정이 꺼졌을 수 있음
        if not params.get_bool("LogAutoUpload"):
          cloudlog.info("log_uploader: upload paused (LogAutoUpload disabled)")
          break

        success = upload_segment(seg_dir, server_url)
        if success:
          mark_uploaded(seg_dir)
        else:
          cloudlog.warning(f"log_uploader: skipping marker for {seg_dir} due to upload failure")

    except Exception as e:
      cloudlog.exception(f"log_uploader: unexpected error: {e}")

    time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
  main()
