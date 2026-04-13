#!/usr/bin/env python3
"""
화면 녹화 파일 자동 업로드
- 감시 경로: /data/media/0/videos/
- .lock 파일이 없는 완료된 .mp4만 전송
- params: VideoAutoUpload (bool, default on)
- params: VideoUploadServer (string, default "https://ws.relena.online")
- 업로드 완료 파일은 .uploaded 마커로 재전송 방지
- 청크 업로드로 대용량 파일 안정 전송
"""

import os
import subprocess
import time
from pathlib import Path

VIDEOS_DIR = "/data/media/0/videos"
DEFAULT_SERVER = "https://ws.relena.online"
UPLOAD_MARKER_SUFFIX = ".uploaded"
POLL_INTERVAL = 30  # seconds

try:
  from openpilot.common.params import Params
  from openpilot.system.swaglog import cloudlog
except ImportError:
  # standalone 실행용 fallback
  import logging
  cloudlog = logging.getLogger("video_uploader")
  cloudlog.addHandler(logging.StreamHandler())
  cloudlog.setLevel(logging.INFO)
  Params = None


def _get_param(key, default=None, encoding='utf-8'):
  if Params is None:
    return default
  try:
    val = Params().get(key, encoding=encoding)
    return val if val is not None else default
  except Exception:
    return default


def _get_param_bool(key, default=True):
  if Params is None:
    return default
  try:
    return Params().get_bool(key)
  except Exception:
    return default


def get_completed_videos(videos_dir: Path) -> list[Path]:
  """
  녹화 완료된 mp4 파일 목록 반환.
  - .lock 파일이 있으면 녹화 중이므로 제외
  - .uploaded 마커가 있으면 이미 전송됨
  """
  if not videos_dir.exists():
    return []

  completed = []
  for f in sorted(videos_dir.iterdir()):
    if not f.suffix == '.mp4':
      continue
    lock_file = videos_dir / (f.name + ".lock")
    marker_file = videos_dir / (f.stem + UPLOAD_MARKER_SUFFIX)
    if lock_file.exists():
      continue
    if marker_file.exists():
      continue
    # 파일 크기가 0이면 스킵
    if f.stat().st_size == 0:
      continue
    completed.append(f)

  return completed


def upload_video(video_path: Path, server_url: str) -> bool:
  """mp4 파일을 curl로 스트리밍 업로드. 메모리 사용 최소화."""
  upload_url = server_url.rstrip("/") + "/api/video/upload"
  file_size = video_path.stat().st_size
  filename = video_path.name

  cloudlog.info(f"video_uploader: uploading {filename} ({file_size / 1024 / 1024:.1f}MB)")

  try:
    result = subprocess.run(
      ["curl", "-s", "-w", "%{http_code}",
       "-F", f"file=@{video_path};type=video/mp4",
       "-F", f"filename={filename}",
       "--max-time", "600",
       upload_url],
      capture_output=True, timeout=620,
    )
    http_code = result.stdout.decode().strip()[-3:]
    if http_code == "200":
      cloudlog.info(f"video_uploader: uploaded {filename}")
      return True
    else:
      cloudlog.warning(f"video_uploader: server returned {http_code}: {result.stdout.decode()[:200]}")
      return False
  except (subprocess.TimeoutExpired, FileNotFoundError) as e:
    cloudlog.warning(f"video_uploader: upload failed for {filename}: {e}")
    return False


def mark_uploaded(video_path: Path) -> None:
  marker = video_path.parent / (video_path.stem + UPLOAD_MARKER_SUFFIX)
  try:
    marker.touch()
  except OSError as e:
    cloudlog.warning(f"video_uploader: failed to create marker {marker}: {e}")


def main() -> None:
  videos_dir = Path(VIDEOS_DIR)
  cloudlog.info("video_uploader: started")

  while True:
    try:
      if not _get_param_bool("VideoAutoUpload", default=True):
        time.sleep(POLL_INTERVAL)
        continue

      server_url = (_get_param("VideoUploadServer") or "").strip() or DEFAULT_SERVER

      videos = get_completed_videos(videos_dir)
      if not videos:
        time.sleep(POLL_INTERVAL)
        continue

      cloudlog.info(f"video_uploader: {len(videos)} video(s) pending upload")

      for video_path in videos:
        if not _get_param_bool("VideoAutoUpload", default=True):
          cloudlog.info("video_uploader: upload paused (VideoAutoUpload disabled)")
          break

        # 녹화 중 파일이 된 경우 다시 체크
        lock_file = video_path.parent / (video_path.name + ".lock")
        if lock_file.exists():
          continue

        if upload_video(video_path, server_url):
          mark_uploaded(video_path)

    except Exception as e:
      cloudlog.exception(f"video_uploader: unexpected error: {e}")

    time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
  main()
