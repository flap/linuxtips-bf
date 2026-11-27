#!/usr/bin/env bash
# Mede o tempo de resposta de uma URL com curl e, opcionalmente, grava em CSV.
#
# Uso:
#   scripts/check-response.sh [--url URL] [--samples N] [--csv ARQUIVO] [--source NOME]
#
# Variáveis de ambiente (os flags têm prioridade):
#   SITE_URL       URL a medir                       (padrão: https://pimenta.dev)
#   SAMPLES        número de requisições             (padrão: 5)
#   MAX_TTFB_MS    limite da mediana do TTFB em ms   (padrão: 800)
#   MAX_TOTAL_MS   limite da mediana do total em ms  (padrão: 1500)
#   TIMEOUT_S      timeout por requisição em s       (padrão: 15)
#   CSV_FILE       se definido, grava as medições nesse CSV
#   SOURCE         origem da medição gravada no CSV  (padrão: manual)
#
# Códigos de saída:
#   0  tudo dentro dos limites
#   1  erro de uso
#   2  limite estourado ou requisição com falha (status fora de 2xx/3xx)

set -euo pipefail

URL="${SITE_URL:-https://pimenta.dev}"
SAMPLES="${SAMPLES:-5}"
MAX_TTFB_MS="${MAX_TTFB_MS:-800}"
MAX_TOTAL_MS="${MAX_TOTAL_MS:-1500}"
TIMEOUT_S="${TIMEOUT_S:-15}"
CSV_FILE="${CSV_FILE:-}"
SOURCE="${SOURCE:-manual}"

CSV_HEADER="timestamp_utc,source,url,http_code,dns_ms,connect_ms,tls_ms,ttfb_ms,total_ms"

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)     URL="${2:?}"; shift 2 ;;
    --samples) SAMPLES="${2:?}"; shift 2 ;;
    --csv)     CSV_FILE="${2:?}"; shift 2 ;;
    --source)  SOURCE="${2:?}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Opção desconhecida: $1" >&2; usage ;;
  esac
done

for n in "$SAMPLES" "$MAX_TTFB_MS" "$MAX_TOTAL_MS" "$TIMEOUT_S"; do
  [[ "$n" =~ ^[0-9]+$ && "$n" -gt 0 ]] || { echo "Valor numérico inválido: $n" >&2; exit 1; }
done
[[ "$URL" =~ ^https?:// ]] || { echo "A URL precisa começar com http:// ou https://" >&2; exit 1; }
[[ "$URL" != *,* && "$SOURCE" != *,* ]] || { echo "URL e source não podem conter vírgula" >&2; exit 1; }

if [[ -n "$CSV_FILE" ]]; then
  mkdir -p "$(dirname "$CSV_FILE")"
  [[ -s "$CSV_FILE" ]] || echo "$CSV_HEADER" > "$CSV_FILE"
fi

measure() {
  # Em caso de erro de rede o curl ainda imprime o -w com http_code 000.
  curl -o /dev/null -sS -L --proto '=http,https' --max-time "$TIMEOUT_S" \
    -w '%{http_code} %{time_namelookup} %{time_connect} %{time_appconnect} %{time_starttransfer} %{time_total}' \
    "$URL" 2>/dev/null || true
}

median() { sort -n | awk '{a[NR]=$1} END {if (NR==0) print 0; else if (NR%2) print a[(NR+1)/2]; else printf "%.0f\n", (a[NR/2]+a[NR/2+1])/2}'; }

ttfbs=()
totals=()
failures=0

printf '%-20s %-4s %8s %8s %8s %8s %8s\n' "timestamp_utc" "http" "dns_ms" "conn_ms" "tls_ms" "ttfb_ms" "total_ms"
for ((i = 1; i <= SAMPLES; i++)); do
  raw="$(measure)"
  [[ -n "$raw" ]] || raw="000 0 0 0 0 0"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  read -r code dns conn tls ttfb total < <(
    echo "$raw" | awk '{printf "%s %.0f %.0f %.0f %.0f %.0f\n", $1, $2*1000, $3*1000, $4*1000, $5*1000, $6*1000}'
  )

  printf '%-20s %-4s %8s %8s %8s %8s %8s\n' "$ts" "$code" "$dns" "$conn" "$tls" "$ttfb" "$total"
  [[ -n "$CSV_FILE" ]] && echo "$ts,$SOURCE,$URL,$code,$dns,$conn,$tls,$ttfb,$total" >> "$CSV_FILE"

  if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
    ttfbs+=("$ttfb")
    totals+=("$total")
  else
    failures=$((failures + 1))
  fi
  (( i < SAMPLES )) && sleep 1
done

med_ttfb="$(printf '%s\n' "${ttfbs[@]:-}" | { grep -v '^$' || true; } | median)"
med_total="$(printf '%s\n' "${totals[@]:-}" | { grep -v '^$' || true; } | median)"

echo
echo "URL: $URL"
echo "Amostras: $SAMPLES | falhas: $failures"
echo "Mediana TTFB: ${med_ttfb} ms (limite ${MAX_TTFB_MS} ms)"
echo "Mediana total: ${med_total} ms (limite ${MAX_TOTAL_MS} ms)"
[[ -n "$CSV_FILE" ]] && echo "Gravado em: $CSV_FILE"

status=0
if (( failures > 0 )); then
  echo "ALERTA: $failures requisição(ões) falharam ou retornaram status fora de 2xx/3xx"
  status=2
fi
if (( ${#ttfbs[@]} > 0 && med_ttfb > MAX_TTFB_MS )); then
  echo "ALERTA: mediana do TTFB acima do limite"
  status=2
fi
if (( ${#totals[@]} > 0 && med_total > MAX_TOTAL_MS )); then
  echo "ALERTA: mediana do tempo total acima do limite"
  status=2
fi
(( status == 0 )) && echo "OK: dentro dos limites"
exit "$status"
