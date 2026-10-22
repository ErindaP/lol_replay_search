#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <match_id> <output_replay_path>" >&2
  exit 2
fi

MATCH_ID="$1"
OUTPUT_PATH="$2"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck source=/dev/null
  . "${ENV_FILE}"
  set +a
fi

URL_TEMPLATE="${REPLAY_DOWNLOAD_URL_TEMPLATE:-}"
if [ -z "${URL_TEMPLATE}" ]; then
  echo "REPLAY_DOWNLOAD_URL_TEMPLATE is not set in ${ENV_FILE}" >&2
  echo "Set it to your replay source endpoint template." >&2
  exit 1
fi

RIOT_REGION="${RIOT_REGION:-europe}"
RIOT_PLATFORM="${RIOT_PLATFORM:-euw1}"
URL="${URL_TEMPLATE//\{match_id\}/${MATCH_ID}}"
URL="${URL//\{riot_region\}/${RIOT_REGION}}"
URL="${URL//\{riot_platform\}/${RIOT_PLATFORM}}"

mkdir -p "$(dirname -- "${OUTPUT_PATH}")"
TMP_PATH="${OUTPUT_PATH}.part"

CURL_ARGS=(--fail --silent --show-error --location --retry 2 --connect-timeout 10 --max-time 120)
if [ -n "${RIOT_API_KEY:-}" ]; then
  CURL_ARGS+=(--header "X-Riot-Token: ${RIOT_API_KEY}")
fi

curl "${CURL_ARGS[@]}" --output "${TMP_PATH}" "${URL}"

if [ ! -s "${TMP_PATH}" ]; then
  echo "Downloaded file is empty: ${TMP_PATH}" >&2
  exit 1
fi

mv -f "${TMP_PATH}" "${OUTPUT_PATH}"
echo "Fetched replay ${MATCH_ID} -> ${OUTPUT_PATH}"
