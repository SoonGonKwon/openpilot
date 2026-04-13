#!/usr/bin/env bash
# Tailscale 자동 설치 스크립트 (aarch64)
# 바이너리가 없을 때만 설치, 있으면 패스

TAILSCALE_DIR="/data/tailscale"
TAILSCALE_BIN="$TAILSCALE_DIR/tailscale"
TAILSCALED_BIN="$TAILSCALE_DIR/tailscaled"

# 이미 설치되어 있으면 패스
if [ -x "$TAILSCALE_BIN" ] && [ -x "$TAILSCALED_BIN" ]; then
  exit 0
fi

echo "[tailscale] Binary not found, installing..."

# 네트워크 대기 (최대 30초)
for i in $(seq 1 30); do
  if curl -sf --max-time 3 https://pkgs.tailscale.com > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

# 최신 stable 버전 다운로드 (aarch64)
TAILSCALE_VERSION=$(curl -sf https://pkgs.tailscale.com/stable/ | grep -oP 'tailscale_\K[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [ -z "$TAILSCALE_VERSION" ]; then
  TAILSCALE_VERSION="1.80.2"  # fallback 버전
fi

TARBALL_URL="https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_arm64.tgz"
TMP_DIR=$(mktemp -d)

echo "[tailscale] Downloading v${TAILSCALE_VERSION} from ${TARBALL_URL}..."
if ! curl -fsSL --max-time 120 "$TARBALL_URL" -o "$TMP_DIR/tailscale.tgz"; then
  echo "[tailscale] Download failed"
  rm -rf "$TMP_DIR"
  exit 1
fi

tar xzf "$TMP_DIR/tailscale.tgz" -C "$TMP_DIR" 2>/dev/null
EXTRACTED_DIR=$(find "$TMP_DIR" -maxdepth 1 -type d -name "tailscale_*" | head -1)

if [ -z "$EXTRACTED_DIR" ]; then
  echo "[tailscale] Extraction failed"
  rm -rf "$TMP_DIR"
  exit 1
fi

mkdir -p "$TAILSCALE_DIR/state"
cp "$EXTRACTED_DIR/tailscale" "$TAILSCALE_BIN"
cp "$EXTRACTED_DIR/tailscaled" "$TAILSCALED_BIN"
chmod +x "$TAILSCALE_BIN" "$TAILSCALED_BIN"

rm -rf "$TMP_DIR"
echo "[tailscale] Installed v${TAILSCALE_VERSION} to ${TAILSCALE_DIR}"
