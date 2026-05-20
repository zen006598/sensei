#!/usr/bin/env bash
# Download the Piper voice model into the `piper_models` named volume.
# Run once after first deploy of the TTS feature.
#
# Usage:
#   ./scripts/fetch-piper-voice.sh                       # en_US-libritts-high
#   PIPER_VOICE=en_US-amy-medium ./scripts/fetch-piper-voice.sh
set -euo pipefail

VOICE="${PIPER_VOICE:-en_US-libritts-high}"
QUALITY="${VOICE##*-}"                  # "high", "medium", "low"
LOCALE="${VOICE%-*}"                    # "en_US-libritts"
LANG="${LOCALE%%_*}"                    # "en"
REGION_VOICE="${LOCALE#*_}"             # "US-libritts"
REGION="${REGION_VOICE%%-*}"            # "US"
SPEAKER="${REGION_VOICE#*-}"            # "libritts"

BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${LANG}/${LANG}_${REGION}/${SPEAKER}/${QUALITY}"

echo "Fetching ${VOICE} from ${BASE} ..."

# Use the existing sensei service (curl is already installed in its image)
# to write into the piper_models volume. --no-deps avoids starting other
# services; --volume overrides the :ro mount mode from compose to allow
# writes during this one-off operation; --entrypoint runs sh instead of
# the bot's default CMD.
docker compose run --rm \
  --no-deps \
  --volume "piper_models:/data/piper:rw" \
  --entrypoint sh \
  sensei -c "
    curl -fL -o /data/piper/${VOICE}.onnx '${BASE}/${VOICE}.onnx'
    curl -fL -o /data/piper/${VOICE}.onnx.json '${BASE}/${VOICE}.onnx.json'
    ls -lh /data/piper/
  "

echo "Done. Restart sensei: docker compose up -d --force-recreate sensei"
