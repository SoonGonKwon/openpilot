# [RADAR_TRACK_TEST] This file is for radar track testing only - remove when done

import asyncio
import json
import signal
import time

import cereal.messaging as messaging
import websockets

WS_URL = "ws://ws.relena.online/ws"
SEND_INTERVAL = 0.1
RECONNECT_DELAY = 5

shutdown_event = asyncio.Event()


def handle_sigterm(*args):
    print("[radar_data_sender] SIGTERM received, shutting down...")
    shutdown_event.set()


signal.signal(signal.SIGTERM, handle_sigterm)


def collect_data(sm):
    radar_state = sm["radarState"]
    car_state = sm["carState"]

    lead = None
    if radar_state.leadOne.status:
        lo = radar_state.leadOne
        lead = {
            "drel": lo.dRel,
            "vrel": lo.vRel,
            "track_id": lo.trackId,
            "status": lo.status,
        }

    radar_points = []
    for pt in radar_state.radarPoints:
        radar_points.append({
            "track_id": pt.trackId,
            "drel": pt.dRel,
            "vrel": pt.vRel,
            "yRel": pt.yRel,
        })

    return {
        "timestamp": time.time(),
        "car_speed": car_state.vEgo * 3.6,
        "radar_enabled": radar_state.radarEnabled,
        "lead": lead,
        "radar_points": radar_points,
    }


async def sender_loop():
    sm = messaging.SubMaster(["radarState", "carState", "controlsState"])

    while not shutdown_event.is_set():
        try:
            async with websockets.connect(WS_URL) as ws:
                print(f"[radar_data_sender] Connected to {WS_URL}")
                try:
                    while not shutdown_event.is_set():
                        sm.update(0)
                        data = collect_data(sm)
                        payload = json.dumps(data)
                        await ws.send(payload)
                        await asyncio.sleep(SEND_INTERVAL)
                except websockets.ConnectionClosed:
                    print("[radar_data_sender] Connection closed, reconnecting...")
        except Exception as e:
            print(f"[radar_data_sender] Connection failed: {e}. Retrying in {RECONNECT_DELAY}s...")
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.ensure_future(shutdown_event.wait())),
                    timeout=RECONNECT_DELAY,
                )
            except asyncio.TimeoutError:
                pass

    print("[radar_data_sender] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(sender_loop())
