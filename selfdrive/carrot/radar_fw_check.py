# [RADAR_TRACK_TEST] This file is for radar track firmware diagnosis - remove when done
"""
레이더 펌웨어 버전 확인 + 설정값 읽기 + 서버 전송
openpilot 실행 중에 동작 (IsoTpParallelQuery 사용)

트리거: Params "CheckRadarFirmware" = "1"
결과:   Params "RadarFirmwareResult" 에 JSON 저장 + ws.relena.online 전송
"""

import json
import time
import threading
import requests
from openpilot.common.params import Params

WS_SERVER_URL = "http://ws.relena.online/radar-fw"  # HTTP POST endpoint


def check_radar_firmware(CP, logcan, sendcan):
  """
  레이더 펌웨어 버전 및 현재 설정값을 읽어 서버로 전송.
  interface.py의 init_car_interface()에서 호출.
  """
  from opendbc.car.isotp_parallel_query import IsoTpParallelQuery
  from opendbc.car.hyundai.values import HyundaiFlags

  result = {
    "car_fingerprint": CP.carFingerprint,
    "timestamp": time.time(),
    "radar_address": 0x7D0,
    "fw_version": None,
    "fw_version_hex": None,
    "current_config": None,
    "current_config_hex": None,
    "tracks_config": None,
    "radar_tracks_possible": False,
    "error": None,
  }

  sccBus = 2 if CP.flags & HyundaiFlags.CAMERA_SCC.value else 0

  try:
    # Step 1: 펌웨어 버전 읽기 (0xf100)
    print("[RadarFwCheck] Reading firmware version...")
    query = IsoTpParallelQuery(sendcan, logcan, sccBus, [0x7D0],
                               [b'\x10\x07'], [b'\x50\x07'])
    session_ok = query.get_data(0.5)

    if not session_ok:
      result["error"] = "Failed to open diagnostic session"
      _send_result(result)
      return result

    # 펌웨어 버전 읽기
    READ_REQUEST = b'\x22'
    FW_DATA_ID = b'\xf1\x00'
    query = IsoTpParallelQuery(sendcan, logcan, sccBus, [0x7D0],
                               [READ_REQUEST + FW_DATA_ID], [b'\x62'])
    fw_data = query.get_data(0.5)

    for addr, data in fw_data.items():
      result["fw_version"] = data.decode('latin-1', errors='replace').strip()
      result["fw_version_hex"] = data.hex()
      print(f"[RadarFwCheck] FW Version: {result['fw_version']}")
      break

    # Step 2: 현재 설정값 읽기 (0x0142)
    CONFIG_DATA_ID = b'\x01\x42'
    query = IsoTpParallelQuery(sendcan, logcan, sccBus, [0x7D0],
                               [READ_REQUEST + CONFIG_DATA_ID], [b'\x62'])
    config_data = query.get_data(0.5)

    for addr, data in config_data.items():
      result["current_config"] = list(data)
      result["current_config_hex"] = data.hex()
      print(f"[RadarFwCheck] Current Config: 0x{data.hex()}")
      break

    # Step 3: 알려진 펌웨어 목록과 비교
    result["tracks_config"] = _get_tracks_config(result["current_config"])
    result["radar_tracks_possible"] = result["tracks_config"] is not None

    print(f"[RadarFwCheck] Radar tracks possible: {result['radar_tracks_possible']}")

  except Exception as e:
    result["error"] = str(e)
    print(f"[RadarFwCheck] Error: {e}")

  # Params에 결과 저장
  params = Params()
  params.put("RadarFirmwareResult", json.dumps(result))

  # 서버로 전송 (별도 스레드)
  threading.Thread(target=_send_result, args=(result,), daemon=True).start()

  return result


def _get_tracks_config(current_config):
  """현재 config에서 tracks enabled 버전 반환. 알 수 없으면 None."""
  if current_config is None:
    return None

  # 알려진 패턴: 마지막 바이트 0x00 → 0x01 이 tracks enable
  if len(current_config) >= 6 and current_config[-1] == 0x00:
    tracks_config = list(current_config)
    tracks_config[-1] = 0x01
    return tracks_config

  if len(current_config) >= 6 and current_config[-1] == 0x01:
    # 이미 활성화된 상태
    return current_config

  return None


def _send_result(result):
  """서버 HTTP POST로 결과 전송"""
  try:
    resp = requests.post(
      WS_SERVER_URL,
      json=result,
      timeout=10
    )
    print(f"[RadarFwCheck] Server response: {resp.status_code}")
  except Exception as e:
    print(f"[RadarFwCheck] Failed to send to server: {e}")


def run_check_if_requested(CP, logcan, sendcan):
  """
  interface.py init_car_interface()에서 호출.
  Params "CheckRadarFirmware" == "1" 일 때만 실행.
  """
  params = Params()
  if params.get("CheckRadarFirmware", encoding='utf-8') == "1":
    print("[RadarFwCheck] Firmware check requested, running...")
    params.put("CheckRadarFirmware", "0")  # 한 번만 실행
    result = check_radar_firmware(CP, logcan, sendcan)
    return result
  return None
