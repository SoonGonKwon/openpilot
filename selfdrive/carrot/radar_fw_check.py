# [RADAR_TRACK_TEST] This file is for radar track firmware diagnosis - remove when done
"""
레이더 제조사 자동 탐지 + 펌웨어 읽기 + CAN 스캔 + DBC 스켈레톤 생성 + 서버 전송

지원 제조사: Mando, Bosch, Continental (자동 탐지)
트리거: Params "CheckRadarFirmware" = "1"
결과:   Params "RadarFirmwareResult" 에 JSON 저장 + ws.relena.online 전송
"""

import json
import time
import threading
import requests
from collections import defaultdict
from openpilot.common.params import Params

WS_SERVER_URL = "http://ws.relena.online/radar-fw"

# ── 제조사별 UDS 후보 주소 ──────────────────────────────────────────────
RADAR_CANDIDATES = [
  {"name": "Mando",       "addr": 0x7D0, "session": b'\x10\x07', "sess_resp": b'\x50\x07'},
  {"name": "Bosch",       "addr": 0x757, "session": b'\x10\x03', "sess_resp": b'\x50\x03'},
  {"name": "Bosch_alt",   "addr": 0x7CF, "session": b'\x10\x03', "sess_resp": b'\x50\x03'},
  {"name": "Continental", "addr": 0x7B0, "session": b'\x10\x03', "sess_resp": b'\x50\x03'},
  {"name": "Continental2","addr": 0x76F, "session": b'\x10\x03', "sess_resp": b'\x50\x03'},
  {"name": "Generic",     "addr": 0x7D5, "session": b'\x10\x03', "sess_resp": b'\x50\x03'},
]

# 제조사 판별 키워드 (펌웨어 문자열 포함 여부로 판단)
MANUFACTURER_KEYWORDS = {
  "Mando":       ["SCC", "FHCUP", "F-CUP", "FHCU"],
  "Bosch":       ["BOSCH", "BSH", "0 445"],
  "Continental": ["CONT", "CTL", "ARS", "SRR"],
}

# 알려진 Mando config 패턴 (마지막 바이트 0→1 이 tracks enable)
MANDO_DEFAULT_CONFIG  = bytes([0x00, 0x00, 0x00, 0x01, 0x00, 0x00])
MANDO_TRACKS_CONFIG   = bytes([0x00, 0x00, 0x00, 0x01, 0x00, 0x01])
MANDO_CONFIG_DATA_ID  = b'\x01\x42'
FW_DATA_ID            = b'\xf1\x00'


def check_radar_firmware(CP, logcan, sendcan):
  from opendbc.car.isotp_parallel_query import IsoTpParallelQuery
  from opendbc.car.hyundai.values import HyundaiFlags

  sccBus = 2 if CP.flags & HyundaiFlags.CAMERA_SCC.value else 0

  result = {
    "car_fingerprint": CP.carFingerprint,
    "timestamp": time.time(),
    "manufacturer": None,
    "radar_addr": None,
    "fw_version": None,
    "fw_version_hex": None,
    "current_config_hex": None,
    "tracks_config_hex": None,
    "radar_tracks_possible": False,
    "can_scan": [],        # 발견된 CAN 주소 목록
    "dbc_skeleton": None,  # 자동 생성 DBC 스켈레톤
    "error": None,
  }

  try:
    # ── 1단계: 제조사별 UDS 주소 탐색 ──────────────────────────────────
    detected = _probe_radar_address(logcan, sendcan, sccBus)

    if detected:
      result["manufacturer"] = detected["name"]
      result["radar_addr"]   = hex(detected["addr"])
      print(f"[RadarFwCheck] Detected: {detected['name']} @ {hex(detected['addr'])}")

      # ── 2단계: 펌웨어 버전 읽기 ───────────────────────────────────────
      fw, fw_hex = _read_data_by_id(logcan, sendcan, sccBus,
                                    detected["addr"], FW_DATA_ID)
      result["fw_version"]     = fw
      result["fw_version_hex"] = fw_hex
      print(f"[RadarFwCheck] FW: {fw}")

      # 펌웨어 문자열로 제조사 재확인
      if fw:
        result["manufacturer"] = _identify_manufacturer(fw) or detected["name"]

      # ── 3단계: 만도인 경우 config 읽기 ────────────────────────────────
      if result["manufacturer"] == "Mando":
        cfg, cfg_hex = _read_data_by_id(logcan, sendcan, sccBus,
                                        detected["addr"], MANDO_CONFIG_DATA_ID)
        result["current_config_hex"] = cfg_hex
        if cfg:
          tracks_cfg = _get_mando_tracks_config(cfg)
          result["tracks_config_hex"]    = tracks_cfg.hex() if tracks_cfg else None
          result["radar_tracks_possible"] = tracks_cfg is not None
        print(f"[RadarFwCheck] Config: {cfg_hex}, tracks_possible={result['radar_tracks_possible']}")

    else:
      result["error"] = "No radar responded to UDS probe"
      print("[RadarFwCheck] No radar found via UDS")

    # ── 4단계: CAN bus 1 스캔 (주기적 메시지 수집) ────────────────────
    print("[RadarFwCheck] Scanning CAN bus 1 for radar messages...")
    can_addrs = _scan_can_bus(logcan, bus=sccBus, duration=2.0)
    result["can_scan"] = can_addrs
    print(f"[RadarFwCheck] CAN scan found {len(can_addrs)} addresses: {[hex(a) for a in can_addrs]}")

    # ── 5단계: DBC 스켈레톤 생성 ──────────────────────────────────────
    if can_addrs:
      result["dbc_skeleton"] = _generate_dbc_skeleton(
        can_addrs,
        result["manufacturer"] or "Unknown",
        CP.carFingerprint
      )
      print("[RadarFwCheck] DBC skeleton generated")

  except Exception as e:
    result["error"] = str(e)
    print(f"[RadarFwCheck] Error: {e}")

  # Params 저장
  params = Params()
  params.put("RadarFirmwareResult", json.dumps(result, ensure_ascii=False))

  # 서버 전송 (별도 스레드)
  threading.Thread(target=_send_result, args=(result,), daemon=True).start()

  return result


def _probe_radar_address(logcan, sendcan, bus):
  """후보 UDS 주소들을 순서대로 시도해서 응답하는 첫 번째 반환"""
  from opendbc.car.isotp_parallel_query import IsoTpParallelQuery

  for candidate in RADAR_CANDIDATES:
    try:
      query = IsoTpParallelQuery(sendcan, logcan, bus,
                                 [candidate["addr"]],
                                 [candidate["session"]],
                                 [candidate["sess_resp"]])
      resp = query.get_data(0.3)
      if resp:
        return candidate
    except Exception:
      continue
  return None


def _read_data_by_id(logcan, sendcan, bus, addr, data_id):
  """UDS Read Data By Identifier (0x22) → (문자열, hex문자열)"""
  from opendbc.car.isotp_parallel_query import IsoTpParallelQuery
  try:
    query = IsoTpParallelQuery(sendcan, logcan, bus,
                               [addr],
                               [b'\x22' + data_id],
                               [b'\x62'])
    resp = query.get_data(0.5)
    for _, data in resp.items():
      text = data.decode('latin-1', errors='replace').strip()
      return text, data.hex()
  except Exception:
    pass
  return None, None


def _identify_manufacturer(fw_string):
  """펌웨어 문자열에서 제조사 추정"""
  fw_upper = fw_string.upper()
  for manufacturer, keywords in MANUFACTURER_KEYWORDS.items():
    if any(kw in fw_upper for kw in keywords):
      return manufacturer
  return None


def _get_mando_tracks_config(config_bytes):
  """만도 config에서 tracks_enabled 버전 반환. 이미 활성화됐거나 패턴 불일치 시 None"""
  if len(config_bytes) < 6:
    return None
  cfg = list(config_bytes)
  if cfg[-1] == 0x00:            # 비활성화 상태 → enable 버전 반환
    cfg[-1] = 0x01
    return bytes(cfg)
  if cfg[-1] == 0x01:            # 이미 활성화됨
    return bytes(cfg)
  return None                    # 알 수 없는 패턴


def _scan_can_bus(logcan, bus, duration=2.0):
  """
  CAN bus에서 주기적으로 수신되는 메시지 주소 수집.
  레이더 트랙 후보 주소 범위를 우선 표시.
  """
  import cereal.messaging as messaging

  addr_counts = defaultdict(int)
  start = time.monotonic()

  # cereal messaging으로 raw CAN 수신
  try:
    sm = messaging.SubMaster(['can'])
    while time.monotonic() - start < duration:
      sm.update(100)
      if sm.updated['can']:
        for msg in sm['can']:
          if msg.src == bus:
            addr_counts[msg.address] += 1
  except Exception as e:
    print(f"[RadarFwCheck] CAN scan error: {e}")

  # 2회 이상 수신된 주소만 (노이즈 제거), 주소 오름차순 정렬
  periodic_addrs = sorted([addr for addr, cnt in addr_counts.items() if cnt >= 2])
  return periodic_addrs


def _generate_dbc_skeleton(can_addrs, manufacturer, car_fingerprint):
  """
  발견된 CAN 주소로 DBC 스켈레톤 자동 생성.
  레이더 트랙 후보 범위(0x500~0x51F, 0x210~0x21F, 0x3A5~0x3C4)를 강조.
  """
  lines = []
  lines.append(f'VERSION ""')
  lines.append("")
  lines.append(f"// AUTO-GENERATED DBC SKELETON")
  lines.append(f"// Car: {car_fingerprint}")
  lines.append(f"// Manufacturer guess: {manufacturer}")
  lines.append(f"// Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
  lines.append(f"// Total CAN addresses found on bus: {len(can_addrs)}")
  lines.append("")
  lines.append('NS_ :')
  lines.append('BS_:')
  lines.append('BU_:')
  lines.append("")

  # 레이더 트랙 후보 범위 정의
  track_ranges = [
    (0x500, 0x51F, "MANDO_TRACK"),
    (0x210, 0x21F, "CANFD_TRACK_GROUP1"),
    (0x3A5, 0x3C4, "CANFD_TRACK_GROUP2"),
  ]

  for addr in can_addrs:
    # 이 주소가 어느 레이더 트랙 범위인지 확인
    track_label = ""
    for rng_start, rng_end, rng_name in track_ranges:
      if rng_start <= addr <= rng_end:
        track_label = f"  // *** RADAR TRACK CANDIDATE ({rng_name}) ***"
        break

    msg_name = _addr_to_msg_name(addr, manufacturer)
    lines.append(f"BO_ {addr} {msg_name}: 8 Vector__XXX{track_label}")
    lines.append(f" SG_ SIGNAL_1 : 0|8@1+ (1,0) [0|0] \"\" Vector__XXX")
    lines.append(f" SG_ SIGNAL_2 : 8|8@1+ (1,0) [0|0] \"\" Vector__XXX")
    lines.append(f" SG_ SIGNAL_3 : 16|16@1+ (1,0) [0|0] \"\" Vector__XXX")
    lines.append(f" SG_ SIGNAL_4 : 32|16@1+ (1,0) [0|0] \"\" Vector__XXX")
    lines.append(f" SG_ SIGNAL_5 : 48|16@1+ (1,0) [0|0] \"\" Vector__XXX")
    lines.append("")

  return "\n".join(lines)


def _addr_to_msg_name(addr, manufacturer):
  """주소를 의미있는 메시지 이름으로 변환"""
  track_ranges = {
    range(0x500, 0x520): "RADAR_TRACK",
    range(0x210, 0x220): "RADAR_TRACK_CANFD1",
    range(0x3A5, 0x3C5): "RADAR_TRACK_CANFD2",
  }
  for r, name in track_ranges.items():
    if addr in r:
      idx = addr - r.start
      return f"{name}_{addr:x}"

  # 기타 알려진 주소
  known = {
    0x420: "SCC11", 0x421: "SCC12", 0x50A: "SCC_CONTROL",
    0x316: "LKAS11", 0x340: "EMS16", 0x4F1: "MDPS12",
  }
  if addr in known:
    return known[addr]

  return f"MSG_{addr:03X}"


def _send_result(result):
  try:
    resp = requests.post(WS_SERVER_URL, json=result, timeout=10)
    print(f"[RadarFwCheck] Server response: {resp.status_code}")
  except Exception as e:
    print(f"[RadarFwCheck] Failed to send: {e}")


def run_check_if_requested(CP, logcan, sendcan):
  """interface.py init_car_interface()에서 호출"""
  params = Params()
  if params.get("CheckRadarFirmware", encoding='utf-8') == "1":
    print("[RadarFwCheck] Starting radar detection...")
    params.put("CheckRadarFirmware", "0")
    return check_radar_firmware(CP, logcan, sendcan)
  return None
