#!/usr/bin/env python3
"""Gera um relatório HTML autocontido a partir do CSV de check-response.sh.

Uso:
    python3 scripts/report.py response-times.csv relatorio.html

Só usa a biblioteca padrão. Os gráficos são SVG embutidos, então o HTML
abre sem internet. Os achados de diagnóstico (DNS, cache, peso da página)
ficam no dicionário FINDINGS abaixo, porque vêm de checagens manuais e não
do CSV.
"""

import csv
import html
import statistics
import sys
from collections import OrderedDict
from datetime import datetime, timezone

# Checagens manuais feitas em 2026-11-27 a partir do sandbox do Kiro (Ashburn, Virginia, EUA).
FINDINGS = {
    "checked_at": "27/11/2026",
    "cert_issuer": "Amazon RSA 2048 M04",
    "cert_expires": "2027-04-02",
    "dns_compare": [
        # (host, registro DNS, POP do CloudFront, TTFB mediano em ms, status do cache)
        ("pimenta.dev", "A fixo » 108.158.147.x (TTL 4 h, Google Cloud DNS)", "GRU3 (São Paulo)", 354, "Hit"),
        ("www.pimenta.dev", "CNAME » d3dtmq1gbynkhh.cloudfront.net", "IAD61 (Virginia)", 18, "Miss na 1ª, depois Hit"),
    ],
    "resources": [
        # (nome, bytes atuais, bytes otimizados ou None, observação)
        ("sabores/banner.jpeg", 154808, 20078, "1280×1280 exibido em 170×170 » WebP 340×340"),
        ("profile.jpg (foto)", 106790, 8384, "1066×1600 exibido em 180×180 » WebP 360×360"),
        ("Fonte Inter (woff2)", 48256, None, "Google Fonts, subconjunto latin"),
        ("Fonte Space Grotesk (woff2)", 22288, None, "Google Fonts, subconjunto latin"),
        ("HTML (gzip)", 7419, None, "25 KB sem compressão, CSS e JS embutidos"),
        ("CSS do Google Fonts", 841, None, "bloqueia a renderização"),
    ],
    "headers": [
        ("HTTP/2", "sim", True),
        ("HTTP/3 anunciado (alt-svc)", "sim", True),
        ("Compressão do HTML", "gzip (Brotli não é servido)", None),
        ("Cache do CloudFront", "Hit", True),
        ("Cache-Control no HTML e nas imagens", "ausente", False),
        ("Strict-Transport-Security (HSTS)", "ausente", False),
        ("width/height nas <img>", "ausente", False),
        ("preconnect para Google Fonts", "ausente", False),
    ],
}

IMPROVEMENTS = [
    {
        "title": "Apontar o domínio raiz para o CloudFront por ALIAS, não por IPs fixos",
        "impact": "Alto",
        "effort": "Baixo",
        "problem": (
            "<code>pimenta.dev</code> tem registros A fixos com IPs do ponto de presença do CloudFront em São Paulo (GRU3). "
            "Todo visitante, de qualquer país, é mandado para São Paulo. Do sandbox em Virginia o TTFB foi 354 ms pelo domínio raiz "
            "e 18 ms pelo <code>www.pimenta.dev</code>, que usa o CNAME do CloudFront e cai no ponto mais próximo. "
            "Além da latência, a AWS pode trocar esses IPs a qualquer momento, e o site sai do ar se isso acontecer."
        ),
        "action": (
            "Mover a zona DNS para o Route 53 e criar um registro <code>A</code> do tipo <em>Alias</em> apontando para "
            "<code>d3dtmq1gbynkhh.cloudfront.net</code> (as consultas de Alias para CloudFront não são cobradas). "
            "Outra opção é um provedor de DNS com ALIAS/ANAME ou CNAME flattening. "
            "Para visitantes no Brasil a melhora é pequena, porque eles já caem em São Paulo."
        ),
        "gain": "~300 ms a menos por requisição para quem está longe de São Paulo; elimina o risco de queda por troca de IP.",
    },
    {
        "title": "Redimensionar as imagens e servir em WebP ou AVIF",
        "impact": "Alto",
        "effort": "Baixo",
        "problem": (
            "As duas imagens somam 262 KB, cerca de 77% da página. A foto tem 1066×1600 pixels e aparece em 180×180; "
            "o banner tem 1280×1280 e aparece em 170×170."
        ),
        "action": (
            "Gerar versões no dobro do tamanho exibido (para telas de alta densidade) e usar <code>&lt;picture&gt;</code> "
            "com WebP/AVIF e JPEG de reserva. Testado com as imagens reais: a foto cai de 107 KB para 8,4 KB (WebP) "
            "ou 7,4 KB (AVIF), e o banner de 155 KB para 20 KB (WebP) ou 16 KB (AVIF)."
        ),
        "gain": "262 KB » 28 KB nas imagens; a página total cai de ~340 KB para ~107 KB.",
    },
    {
        "title": "Definir Cache-Control nas respostas",
        "impact": "Médio",
        "effort": "Baixo",
        "problem": (
            "Nenhuma resposta tem <code>Cache-Control</code>. O navegador decide sozinho quanto tempo guardar e, "
            "em visitas repetidas, refaz requisições de validação (ETag) que custam uma ida e volta cada."
        ),
        "action": (
            "No upload para o S3 (ou numa Response Headers Policy do CloudFront): imagens e fontes com "
            "<code>public, max-age=31536000, immutable</code> e nome versionado (ex.: <code>profile.3f9a.webp</code>); "
            "HTML com <code>public, max-age=300</code>."
        ),
        "gain": "Visitas repetidas carregam imagens do cache local, sem nenhuma requisição.",
    },
    {
        "title": "Ajustar as tags <img>",
        "impact": "Médio",
        "effort": "Baixo",
        "problem": "As imagens não têm <code>width</code>/<code>height</code> nem dicas de prioridade.",
        "action": (
            "Adicionar <code>width</code> e <code>height</code> (evita o layout pular, CLS); "
            "<code>fetchpriority=\"high\"</code> na foto, que está no topo; "
            "<code>loading=\"lazy\"</code> e <code>decoding=\"async\"</code> no banner."
        ),
        "gain": "A foto chega antes; o layout não se move enquanto as imagens carregam.",
    },
    {
        "title": "Reduzir o custo das fontes",
        "impact": "Baixo a médio",
        "effort": "Baixo",
        "problem": (
            "As fontes vêm em cadeia: HTML » CSS do Google Fonts (bloqueia a renderização) » arquivos woff2, "
            "em dois domínios extras. Cada domínio novo custa DNS + TCP + TLS."
        ),
        "action": (
            "Mínimo: <code>&lt;link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin&gt;</code> e o mesmo para "
            "<code>fonts.googleapis.com</code>. Melhor: hospedar os dois woff2 (70 KB) no próprio CloudFront com "
            "<code>&lt;link rel=\"preload\" as=\"font\"&gt;</code>, eliminando as conexões extras."
        ),
        "gain": "Texto renderizado mais cedo; menos conexões em redes móveis.",
    },
    {
        "title": "Ativar Brotli no CloudFront",
        "impact": "Baixo",
        "effort": "Baixo",
        "problem": "Mesmo com o navegador pedindo Brotli, o CloudFront entrega gzip.",
        "action": "Na cache policy da distribuição, marcar Brotli como formato de compressão suportado.",
        "gain": "HTML costuma ficar 10–20% menor que em gzip (alguns KB neste site).",
    },
    {
        "title": "Ativar HSTS",
        "impact": "Baixo",
        "effort": "Baixo",
        "problem": "Sem <code>Strict-Transport-Security</code>, quem digita <code>pimenta.dev</code> pode passar por um redirecionamento de HTTP para HTTPS.",
        "action": "Response Headers Policy do CloudFront com <code>max-age=31536000; includeSubDomains</code>.",
        "gain": "Elimina o redirecionamento na primeira visita via HTTP; também é uma melhoria de segurança.",
    },
]

STAGE_COLORS = OrderedDict([
    ("DNS", "#a78bfa"),
    ("TCP", "#22d3ee"),
    ("TLS", "#34d399"),
    ("Espera (1º byte)", "#fbbf24"),
    ("Download", "#f87171"),
])

URL_COLORS = ["#22d3ee", "#f472b6", "#a3e635", "#fbbf24"]


def esc(s):
    return html.escape(str(s))


def pct(values, p):
    """Percentil pelo mesmo método de scripts/summarize.sh."""
    v = sorted(values)
    return v[int((len(v) - 1) * p)]


def load(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "total_ms"):
                r[k] = int(r[k])
            r["ok"] = r["http_code"][:1] in ("2", "3")
            r["ts"] = datetime.strptime(r["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            rows.append(r)
    return rows


def label(url):
    return "Foto (profile.jpg)" if url.endswith("profile.jpg") else ("Página" if url.rstrip("/").endswith("pimenta.dev") else url)


def summarize(rows):
    out = OrderedDict()
    for r in rows:
        out.setdefault((r["source"], r["url"]), []).append(r)
    stats = []
    for (src, url), rs in out.items():
        ok = [r for r in rs if r["ok"]]
        s = {"source": src, "url": url, "label": label(url), "n": len(rs), "fail": len(rs) - len(ok)}
        for k in ("ttfb_ms", "total_ms"):
            vals = [r[k] for r in ok] or [0]
            s[k] = {"avg": round(statistics.mean(vals)), "p50": pct(vals, 0.5), "p95": pct(vals, 0.95), "max": max(vals)}
        med = lambda k: statistics.median([r[k] for r in ok]) if ok else 0
        dns, con, tls, ttfb, tot = (med(k) for k in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "total_ms"))
        s["stages"] = OrderedDict([
            ("DNS", dns), ("TCP", max(con - dns, 0)), ("TLS", max(tls - con, 0)),
            ("Espera (1º byte)", max(ttfb - tls, 0)), ("Download", max(tot - ttfb, 0)),
        ])
        stats.append(s)
    return stats


def svg_timeseries(rows, urls):
    """TTFB (círculo) e total (linha) por amostra, agrupado por coleta."""
    w, h, pl, pr, pt, pb = 900, 300, 50, 20, 20, 50
    ok = [r for r in rows if r["ok"]]
    if not ok:
        return "<p>Sem medições com sucesso.</p>"
    ymax = max(r["total_ms"] for r in ok)
    ymax = ((ymax // 200) + 1) * 200
    n = len(rows)
    x = lambda i: pl + (w - pl - pr) * (i + 0.5) / n
    y = lambda v: pt + (h - pt - pb) * (1 - v / ymax)
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Tempo de resposta por amostra">']
    for v in range(0, ymax + 1, 200):
        parts.append(f'<line x1="{pl}" x2="{w - pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                     f'<text x="{pl - 6}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{v}</text>')
    # separadores por coleta (minuto)
    last = None
    for i, r in enumerate(rows):
        run = r["ts"].strftime("%d/%m %H:%M")
        if run != last:
            parts.append(f'<line x1="{x(i) - (w - pl - pr) / n / 2:.1f}" x2="{x(i) - (w - pl - pr) / n / 2:.1f}" y1="{pt}" y2="{h - pb}" class="sep"/>'
                         f'<text x="{x(i) - (w - pl - pr) / n / 2 + 4:.1f}" y="{h - pb + 18}" class="axis">{run} UTC</text>')
            last = run
    for ci, url in enumerate(urls):
        color = URL_COLORS[ci % len(URL_COLORS)]
        for i, r in enumerate(rows):
            if r["url"] != url:
                continue
            if not r["ok"]:
                parts.append(f'<text x="{x(i):.1f}" y="{y(0) - 4:.1f}" class="fail" text-anchor="middle">X<title>{esc(label(url))} {r["timestamp_utc"]}: falha (HTTP {esc(r["http_code"])})</title></text>')
                continue
            parts.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(r["ttfb_ms"]):.1f}" y2="{y(r["total_ms"]):.1f}" stroke="{color}" stroke-width="3" opacity=".45"/>'
                         f'<circle cx="{x(i):.1f}" cy="{y(r["ttfb_ms"]):.1f}" r="4" fill="{color}"><title>{esc(label(url))} {r["timestamp_utc"]}: TTFB {r["ttfb_ms"]} ms, total {r["total_ms"]} ms</title></circle>'
                         f'<rect x="{x(i) - 4:.1f}" y="{y(r["total_ms"]) - 1:.1f}" width="8" height="2" fill="{color}"/>')
    parts.append(f'<text x="{pl}" y="{h - 8}" class="axis">eixo vertical em ms</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_stages(stats):
    w, bar, gap, pl = 900, 34, 18, 230
    h = len(stats) * (bar + gap) + 40
    total_max = max(sum(s["stages"].values()) for s in stats) or 1
    scale = (w - pl - 70) / total_max
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Etapas do tempo de resposta">']
    for i, s in enumerate(stats):
        yy = 10 + i * (bar + gap)
        parts.append(f'<text x="{pl - 10}" y="{yy + bar / 2 + 5}" class="lbl" text-anchor="end">{esc(s["label"])} · {esc(s["source"])}</text>')
        xx = pl
        for name, v in s["stages"].items():
            bw = v * scale
            if bw <= 0:
                continue
            parts.append(f'<rect x="{xx:.1f}" y="{yy}" width="{bw:.1f}" height="{bar}" fill="{STAGE_COLORS[name]}"><title>{name}: {v:.0f} ms</title></rect>')
            if bw > 34:
                parts.append(f'<text x="{xx + bw / 2:.1f}" y="{yy + bar / 2 + 4}" class="inbar" text-anchor="middle">{v:.0f}</text>')
            xx += bw
        parts.append(f'<text x="{xx + 8:.1f}" y="{yy + bar / 2 + 5}" class="lbl">{sum(s["stages"].values()):.0f} ms</text>')
    lx = pl
    for name, c in STAGE_COLORS.items():
        parts.append(f'<rect x="{lx}" y="{h - 22}" width="12" height="12" fill="{c}"/><text x="{lx + 16}" y="{h - 12}" class="axis">{name}</text>')
        lx += 130
    parts.append("</svg>")
    return "".join(parts)


def svg_weights(resources):
    w, bar, gap, pl = 900, 24, 12, 230
    h = len(resources) * (bar + gap) + 36
    mx = max(r[1] for r in resources)
    scale = (w - pl - 150) / mx
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Peso dos recursos">']
    for i, (name, cur, opt, _) in enumerate(resources):
        yy = 6 + i * (bar + gap)
        parts.append(f'<text x="{pl - 10}" y="{yy + bar / 2 + 5}" class="lbl" text-anchor="end">{esc(name)}</text>'
                     f'<rect x="{pl}" y="{yy}" width="{cur * scale:.1f}" height="{bar}" fill="#f87171" opacity=".85"><title>atual: {cur / 1000:.1f} KB</title></rect>')
        if opt:
            parts.append(f'<rect x="{pl}" y="{yy + 5}" width="{opt * scale:.1f}" height="{bar - 10}" fill="#34d399"><title>otimizado: {opt / 1000:.1f} KB</title></rect>')
        txt = f"{cur / 1000:.1f} KB" + (f" » {opt / 1000:.1f} KB" if opt else "")
        parts.append(f'<text x="{pl + cur * scale + 8:.1f}" y="{yy + bar / 2 + 5}" class="lbl">{txt}</text>')
    parts.append(f'<rect x="{pl}" y="{h - 22}" width="12" height="12" fill="#f87171"/><text x="{pl + 16}" y="{h - 12}" class="axis">atual</text>'
                 f'<rect x="{pl + 90}" y="{h - 22}" width="12" height="12" fill="#34d399"/><text x="{pl + 106}" y="{h - 12}" class="axis">otimizado (medido com as imagens reais)</text>')
    parts.append("</svg>")
    return "".join(parts)


def render(rows, generated_at):
    stats = summarize(rows)
    urls = list(OrderedDict.fromkeys(r["url"] for r in rows))
    ok_rows = [r for r in rows if r["ok"]]
    availability = 100 * len(ok_rows) / len(rows) if rows else 0
    runs = len({r["ts"].strftime("%Y%m%d%H%M") for r in rows})
    sources = ", ".join(sorted({r["source"] for r in rows}))
    page = next((s for s in stats if s["label"] == "Página"), stats[0])
    photo = next((s for s in stats if s["label"].startswith("Foto")), None)
    exp = datetime.strptime(FINDINGS["cert_expires"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days_left = (exp - generated_at).days
    cur_total = sum(r[1] for r in FINDINGS["resources"])
    opt_total = sum(r[2] or r[1] for r in FINDINGS["resources"])

    cards = [
        ("TTFB mediano · página", f'{page["ttfb_ms"]["p50"]} ms', f'p95 {page["ttfb_ms"]["p95"]} ms · limite 1000 ms'),
        ("Total mediano · página", f'{page["total_ms"]["p50"]} ms', f'p95 {page["total_ms"]["p95"]} ms · limite 1500 ms'),
    ]
    if photo:
        cards.append(("Total mediano · foto", f'{photo["total_ms"]["p50"]} ms', f'{photo["n"]} amostras · 107 KB'))
    cards += [
        ("Disponibilidade", f"{availability:.0f}%", f'{len(ok_rows)} de {len(rows)} requisições com 2xx/3xx'),
        ("Peso da página", f"{cur_total / 1000:.0f} KB", f"~{opt_total / 1000:.0f} KB com as imagens otimizadas"),
        ("Certificado TLS", f"{days_left} dias", f'expira em {exp:%d/%m/%Y} · {FINDINGS["cert_issuer"]}'),
    ]

    def stat_rows():
        for s in stats:
            t, o = s["ttfb_ms"], s["total_ms"]
            yield (f'<tr><td>{esc(s["label"])}</td><td><code>{esc(s["source"])}</code></td><td>{s["n"]}</td><td>{s["fail"]}</td>'
                   f'<td>{t["avg"]}</td><td>{t["p50"]}</td><td>{t["p95"]}</td><td>{t["max"]}</td>'
                   f'<td>{o["avg"]}</td><td>{o["p50"]}</td><td>{o["p95"]}</td><td>{o["max"]}</td></tr>')

    def check_rows():
        for name, value, good in FINDINGS["headers"]:
            cls = {True: "good", False: "bad", None: "warn"}[good]
            word = {True: "OK", False: "Falta", None: "Atenção"}[good]
            yield f'<tr><td>{esc(name)}</td><td><span class="pill {cls}">{word}</span> {esc(value)}</td></tr>'

    def dns_rows():
        for host, rec, pop, ttfb, cache in FINDINGS["dns_compare"]:
            yield (f'<tr><td><code>{esc(host)}</code></td><td>{esc(rec)}</td><td>{esc(pop)}</td>'
                   f'<td><strong>{ttfb} ms</strong></td><td>{esc(cache)}</td></tr>')

    def improvement_cards():
        for i, imp in enumerate(IMPROVEMENTS, 1):
            lvl = imp["impact"].split()[0].lower()
            yield (f'<article class="imp"><header><span class="num">{i}</span><h3>{esc(imp["title"])}</h3>'
                   f'<span class="tag impact-{lvl}">Impacto {esc(imp["impact"].lower())}</span>'
                   f'<span class="tag">Esforço {esc(imp["effort"].lower())}</span></header>'
                   f'<p><strong>Problema.</strong> {imp["problem"]}</p>'
                   f'<p><strong>O que fazer.</strong> {imp["action"]}</p>'
                   f'<p class="gain"><strong>Ganho esperado.</strong> {esc(imp["gain"])}</p></article>')

    period = f'{rows[0]["ts"]:%d/%m/%Y %H:%M} a {rows[-1]["ts"]:%d/%m/%Y %H:%M} UTC' if rows else "-"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório de tempo de resposta · pimenta.dev</title>
<style>
:root{{--bg:#0b1020;--card:#131a2e;--line:#243049;--txt:#e5e9f2;--mut:#93a0b8;--acc:#22d3ee}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt)}}
.wrap{{max-width:1000px;margin:0 auto;padding:32px 20px 64px}}
h1{{margin:0 0 4px;font-size:1.8rem}} h2{{margin:40px 0 12px;font-size:1.25rem;border-bottom:1px solid var(--line);padding-bottom:6px}}
h3{{margin:0;font-size:1.02rem;flex:1}}
.mut{{color:var(--mut)}} code{{background:#1c2540;padding:1px 5px;border-radius:4px;font-size:.88em}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:20px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.card .k{{color:var(--mut);font-size:.82rem}} .card .v{{font-size:1.6rem;font-weight:700;margin:2px 0}} .card .d{{color:var(--mut);font-size:.8rem}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:.9rem}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}} th{{color:var(--mut);font-weight:600;background:#0f1628}}
tr:last-child td{{border-bottom:0}}
.num-t td:nth-child(n+3){{text-align:right;font-variant-numeric:tabular-nums}}
.pill{{display:inline-block;min-width:64px;text-align:center;font-size:.75rem;font-weight:700;border-radius:999px;padding:1px 8px;margin-right:6px;color:#0b1020}}
.pill.good{{background:#34d399}} .pill.bad{{background:#f87171}} .pill.warn{{background:#fbbf24}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 5px 0 4px;vertical-align:baseline}}
.chart{{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}}
.chart .grid{{stroke:var(--line)}} .chart .sep{{stroke:#3b4a6b;stroke-dasharray:4 4}}
.chart .axis{{fill:var(--mut);font-size:11px}} .chart .lbl{{fill:var(--txt);font-size:12px}} .chart .inbar{{fill:#0b1020;font-size:11px;font-weight:700}}
.chart .fail{{fill:#f87171;font-size:14px}}
.callout{{background:#1f1a0b;border:1px solid #6b5216;border-radius:12px;padding:14px 16px;margin:14px 0}}
.imp{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}}
.imp header{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}}
.imp p{{margin:6px 0}} .imp .gain{{color:#a7f3d0}}
.num{{background:var(--acc);color:#0b1020;font-weight:800;border-radius:50%;width:26px;height:26px;display:inline-flex;align-items:center;justify-content:center}}
.tag{{font-size:.75rem;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--mut)}}
.impact-alto{{border-color:#f87171;color:#fca5a5}} .impact-médio{{border-color:#fbbf24;color:#fde68a}} .impact-baixo{{border-color:#34d399;color:#a7f3d0}}
footer{{margin-top:40px;color:var(--mut);font-size:.82rem}}
</style>
</head>
<body><div class="wrap">
<h1>Tempo de resposta · pimenta.dev</h1>
<div class="mut">Período: {esc(period)} · {runs} coleta(s) · {len(rows)} requisições · origem: {esc(sources)}</div>

<div class="cards">
{''.join(f'<div class="card"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="d">{esc(d)}</div></div>' for k, v, d in cards)}
</div>

<h2>Resumo por URL (ms)</h2>
<table class="num-t">
<tr><th>URL</th><th>Origem</th><th>Amostras</th><th>Falhas</th><th>TTFB méd</th><th>p50</th><th>p95</th><th>máx</th><th>Total méd</th><th>p50</th><th>p95</th><th>máx</th></tr>
{''.join(stat_rows())}
</table>

<h2>Cada medição</h2>
<p class="mut">Cores: {' · '.join(f'<span class="sw" style="background:{URL_COLORS[i % len(URL_COLORS)]}"></span>{esc(label(u))}' for i, u in enumerate(urls))}. O ponto é o TTFB, o traço é o tempo total e a barra entre os dois é o download.</p>
{svg_timeseries(rows, urls)}

<h2>Onde o tempo é gasto (mediana)</h2>
<p class="mut">A espera pelo 1º byte é quase igual ao tempo de TCP: equivale a uma ida e volta na rede, ou seja, o CloudFront responde do cache sem demora de servidor. Quase todo o tempo é distância de rede.</p>
{svg_stages(stats)}

<h2>Achado principal: o domínio raiz não usa o roteamento do CloudFront</h2>
<div class="callout">Os dois endereços servem o mesmo site pela mesma distribuição, mas o domínio raiz força todos os visitantes para São Paulo. Medido do sandbox do Kiro em Virginia (EUA), em {esc(FINDINGS["checked_at"])}:</div>
<table>
<tr><th>Endereço</th><th>DNS</th><th>Ponto do CloudFront</th><th>TTFB mediano</th><th>Cache</th></tr>
{''.join(dns_rows())}
</table>

<h2>Peso da página</h2>
{svg_weights(FINDINGS["resources"])}

<h2>Configuração atual</h2>
<table>{''.join(check_rows())}</table>

<h2>Melhorias recomendadas</h2>
<p class="mut">Ordenadas por impacto e esforço. Os ganhos das imagens foram medidos redimensionando as imagens reais do site; os demais são estimativas.</p>
{''.join(improvement_cards())}

<footer>
Gerado em {generated_at:%d/%m/%Y %H:%M} UTC por <code>scripts/report.py</code> a partir de <code>response-times.csv</code> (branch <code>metrics</code>).
As coletas do GitHub Actions rodam em runners nos EUA e não representam visitantes no Brasil.
Referência: <a href="https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-to-cloudfront-distribution.html" style="color:var(--acc)">Route 53 · roteamento para uma distribuição do CloudFront</a>.
</footer>
</div></body></html>
"""


def main():
    if len(sys.argv) != 3:
        sys.exit("Uso: python3 scripts/report.py response-times.csv relatorio.html")
    rows = load(sys.argv[1])
    if not rows:
        sys.exit("CSV sem medições.")
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(render(rows, datetime.now(timezone.utc)))
    print(f"Relatório gravado em {sys.argv[2]} ({len(rows)} medições)")


if __name__ == "__main__":
    main()
