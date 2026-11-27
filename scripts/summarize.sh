#!/usr/bin/env bash
# Resume o histórico gravado por check-response.sh, agrupado por origem (source).
#
# Uso:
#   scripts/summarize.sh ARQUIVO.csv [--last N]
#
#   --last N   considera só as últimas N linhas do CSV (padrão: todas)

set -euo pipefail

CSV="${1:-}"
[[ -n "$CSV" && -f "$CSV" ]] || { echo "Uso: $0 ARQUIVO.csv [--last N]" >&2; exit 1; }
shift
LAST=0
if [[ "${1:-}" == "--last" ]]; then
  LAST="${2:?}"
  [[ "$LAST" =~ ^[0-9]+$ ]] || { echo "--last precisa ser um número" >&2; exit 1; }
fi

rows="$(tail -n +2 "$CSV")"
(( LAST > 0 )) && rows="$(echo "$rows" | tail -n "$LAST")"
[[ -n "$rows" ]] || { echo "CSV sem medições."; exit 0; }

echo "Período: $(echo "$rows" | head -n1 | cut -d, -f1) até $(echo "$rows" | tail -n1 | cut -d, -f1)"
echo

stats() {
  # Lê números (um por linha) e imprime "média p50 p95 máx".
  sort -n | awk '{a[NR]=$1; s+=$1}
    END {
      if (NR == 0) { print "- - - -"; exit }
      p50 = a[int((NR - 1) * 0.50) + 1]
      p95 = a[int((NR - 1) * 0.95) + 1]
      printf "%.0f %d %d %d\n", s / NR, p50, p95, a[NR]
    }'
}

printf '%-16s %7s %7s | %-24s | %-24s\n' "source" "amostras" "falhas" "TTFB ms (méd p50 p95 máx)" "total ms (méd p50 p95 máx)"
for src in $(echo "$rows" | cut -d, -f2 | sort -u); do
  sub="$(echo "$rows" | awk -F, -v s="$src" '$2 == s')"
  ok="$(echo "$sub" | awk -F, '$4 ~ /^[23][0-9][0-9]$/')"
  n="$(echo "$sub" | wc -l)"
  n_ok="$( [[ -n "$ok" ]] && echo "$ok" | wc -l || echo 0)"
  ttfb="$(echo "$ok" | awk -F, 'NF {print $8}' | stats)"
  total="$(echo "$ok" | awk -F, 'NF {print $9}' | stats)"
  printf '%-16s %7d %7d | %-24s | %-24s\n' "$src" "$n" "$((n - n_ok))" "$ttfb" "$total"
done

echo
echo "5 medições mais lentas (TTFB):"
echo "$rows" | sort -t, -k8,8nr | head -n5 | awk -F, '{printf "  %s  %-14s http=%s ttfb=%sms total=%sms\n", $1, $2, $4, $8, $9}'
