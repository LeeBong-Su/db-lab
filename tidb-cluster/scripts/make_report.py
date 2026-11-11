#!/usr/bin/python3
# 단일 vs 3노드 비교 리포트 생성기 (시스템 python3.9 + matplotlib)
# 입력: results/bench.jsonl, results/fault-*.json  출력: results/report.html, results/summary.png
import json, os, base64, io
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

LAB = str(Path(__file__).resolve().parents[1]); R = f"{LAB}/results"   # tidb-cluster/
# 한글 폰트: WSL이면 Windows 맑은 고딕, 없으면 Noto Sans CJK
_family = None
for fp, fam in [("/mnt/c/Windows/Fonts/malgun.ttf", "Malgun Gothic"),
                ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "Noto Sans CJK JP")]:
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp); _family = _family or fam
plt.rcParams["font.family"] = _family or "sans-serif"
plt.rcParams["axes.unicode_minus"] = False
C1, C3 = "#9aa5b1", "#d9534f"   # single / 3node 색

rows = [json.loads(l) for l in open(f"{R}/bench.jsonl")]
runs = [r for r in rows if r["workload"] != "prepare"]
prep = {r["cluster"]: r["secs"] for r in rows if r["workload"] == "prepare"}
def get(cluster, tag, wl, th):
    for r in runs:
        if r["cluster"] == cluster and r.get("tag", "base") == tag and r["workload"] == wl and r["threads"] == th:
            return r
WL = ["oltp_point_select", "oltp_read_only", "oltp_write_only", "oltp_read_write"]
WLS = ["point_select", "read_only", "write_only", "read_write"]

def b64(fig):
    fig.tight_layout(rect=[0, 0, 1, 0.90])
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    return base64.b64encode(buf.getvalue()).decode()

figs = {}
# ── Fig1: 기본 비교 (32스레드, 분할 전) TPS + p95 ──
fig, ax = plt.subplots(1, 2, figsize=(12, 4))
x = range(len(WL)); w = 0.38
for i, (cl, col, lab) in enumerate([("single", C1, "단일 노드"), ("3node", C3, "3노드")]):
    tps = [get(cl, "base", wl, 32)["tps"] for wl in WL]
    p95 = [get(cl, "base", wl, 32)["lat_p95_ms"] for wl in WL]
    xs = [k + (i - 0.5) * w for k in x]
    bars = ax[0].bar(xs, tps, w, color=col, label=lab)
    ax[0].bar_label(bars, fmt="%.0f", fontsize=8)
    bars = ax[1].bar(xs, p95, w, color=col, label=lab)
    ax[1].bar_label(bars, fmt="%.1f", fontsize=8)
for a, t in zip(ax, ["TPS (32 스레드, 높을수록 좋음)", "p95 지연 ms (낮을수록 좋음)"]):
    a.set_xticks(list(x)); a.set_xticklabels(WLS); a.set_title(t); a.legend(); a.set_yscale("log")
fig.suptitle("① 같은 부하(32 스레드)에서 단일 vs 3노드", fontweight="bold")
figs["base"] = fig

# ── Fig2: TiKV별 CPU (3노드 point_select 32) 분할 전/후 = 핫스팟 ──
fig, ax = plt.subplots(1, 2, figsize=(12, 3.8))
for a, tag, title in [(ax[0], "base", "분할 전: store1에 핫 리전 리더 집중"),
                      (ax[1], "split", "SPLIT+scatter 후: 부하 분산")]:
    r = get("3node", tag, "oltp_point_select", 32)
    tk = {k: v for k, v in r["cores_by_unit"].items() if k.startswith("tikv")}
    td = {k: v for k, v in r["cores_by_unit"].items() if k.startswith("tidb")}
    names = list(tk) + list(td); vals = list(tk.values()) + list(td.values())
    bars = a.bar(names, vals, color=["#5b8def"] * 3 + ["#f0ad4e"] * 3)
    a.bar_label(bars, fmt="%.2f", fontsize=8)
    a.axhline(3, ls="--", c="#5b8def", lw=1); a.axhline(2, ls="--", c="#f0ad4e", lw=1)
    a.set_ylim(0, 3.4); a.set_ylabel("사용 코어"); a.set_title(f"{title}\n(TPS {r['tps']:.0f})")
    a.tick_params(axis="x", labelsize=8)
fig.suptitle("② 3노드 point_select 컴포넌트별 CPU (점선 = CPU 상한: TiKV 3, TiDB 2)", fontweight="bold")
figs["hotspot"] = fig

# ── Fig3: 스레드 스케일링 (분할 후 상태 기준) ──
fig, ax = plt.subplots(1, 3, figsize=(15, 3.8))
for a, wl, t in [(ax[0], "oltp_point_select", "point_select TPS"), (ax[1], "oltp_write_only", "write_only TPS"),
                 (ax[2], "oltp_read_write", "read_write TPS")]:
    for si, (cl, col, ls, lab) in enumerate([("single", C1, "-", "단일 노드"), ("3node", C3, "-", "3노드 (TiDB 3대)"),
                             ("3node-1tidb", "#f0ad4e", "--", "3노드 (TiDB 1대)")]):
        pts = [(th, get(cl, "split", wl, th)) for th in (32, 128)]
        pts = [(th, r["tps"]) for th, r in pts if r]
        if pts:
            a.plot([p[0] for p in pts], [p[1] for p in pts], ls, marker="o", color=col, label=lab)
            for th, v in pts: a.annotate(f"{v:.0f}", (th, v), textcoords="offset points", xytext=(6, -12 if si == 0 else 5), fontsize=8, color=col)
    a.set_xticks([32, 128]); a.set_xlim(20, 145); a.set_xlabel("sysbench 스레드"); a.set_title(t); a.legend(fontsize=8)
fig.suptitle("③ 동시성 32 → 128 처리량 (분할 후 상태)", fontweight="bold")
figs["scale"] = fig

# ── Fig5: TiDB 쿼리당 CPU(us) - 같은 일을 하는데 드는 CPU ──
fig, ax = plt.subplots(figsize=(10, 3.6))
labels, vals, cols = [], [], []
for cl, col, lab in [("single", C1, "단일"), ("3node-1tidb", "#f0ad4e", "3노드/TiDB1"), ("3node", C3, "3노드/TiDB3")]:
    for th in (32, 128):
        r = get(cl, "split", "oltp_point_select", th)
        if r:
            td = sum(v for k, v in r["cores_by_unit"].items() if k.startswith("tidb"))
            labels.append(f"{lab}\n{th}스레드"); vals.append(td * 1e6 / r["qps"]); cols.append(col)
bars = ax.bar(labels, vals, color=cols); ax.bar_label(bars, fmt="%.0f", fontsize=9)
ax.set_ylabel("TiDB CPU us / 쿼리"); ax.tick_params(axis="x", labelsize=8)
fig.suptitle("④ point_select 1건당 TiDB CPU 시간 (낮을수록 효율적)", fontweight="bold")
figs["cpq"] = fig

# ── Fig4: 장애 타임라인 ──
fig, ax = plt.subplots(2, 1, figsize=(12, 6), sharex=False)
for a, cl, col, title in [(ax[0], "3node", C3, "3노드: TiKV 1대 크래시 → 복구, PD 리더 크래시 → 복구"),
                          (ax[1], "single", "#555", "단일 노드: 유일한 TiKV 크래시 → 재기동")]:
    p = f"{R}/fault-{cl}.json"
    if not os.path.exists(p): continue
    d = json.load(open(p))
    a.plot([t["t"] for t in d["timeline"]], [t["tps"] for t in d["timeline"]], color=col)
    for e in d["events"]:
        a.axvline(e["t"], ls="--", c="k", lw=0.8)
        a.annotate(f"{e['action']} {e['unit']}", (e["t"], a.get_ylim()[1] * 0.92), fontsize=8, rotation=0, xytext=(3, 0), textcoords="offset points")
    a.set_ylabel("TPS"); a.set_title(title, fontsize=10)
ax[1].set_xlabel("경과 시간(초), read_write 32 스레드, 2초 간격")
fig.suptitle("⑤ 장애 내성", fontweight="bold"); fig.tight_layout()
figs["fault"] = fig

# 요약 PNG: 차트 ①~⑤를 세로로 이어 붙임
def stack(names, out):
    from PIL import Image
    imgs = [Image.open(io.BytesIO(base64.b64decode(b64(figs[n])))) for n in names]
    W = max(i.width for i in imgs); H = sum(i.height for i in imgs)
    canvas = Image.new("RGB", (W, H), "white"); y = 0
    for i in imgs: canvas.paste(i, (0, y)); y += i.height
    canvas.save(out)
try:
    stack(["base", "hotspot", "scale", "cpq", "fault"], f"{R}/summary.png")
except ImportError:
    figs["fault"].savefig(f"{R}/summary.png", dpi=110, bbox_inches="tight")

# 원시 표
def tr(r):
    return (f"<tr><td>{r['cluster']}</td><td>{r.get('tag','base')}</td><td>{r['workload'].replace('oltp_','')}</td>"
            f"<td>{r['threads']}</td><td>{r['tps']:.1f}</td><td>{r['qps']:.0f}</td><td>{r['lat_avg_ms']:.2f}</td>"
            f"<td>{r['lat_p95_ms']:.2f}</td><td>{r['cores_total']:.2f}</td><td>{r['host_cpu_pct']}</td>"
            f"<td>{sum(r['throttled_by_unit'].values())}</td></tr>")
table = "".join(tr(r) for r in runs)
html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>TiDB 단일 vs 3노드 비교</title>
<style>body{{font-family:'Malgun Gothic',sans-serif;max-width:1150px;margin:24px auto;color:#222}}
table{{border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #ccc;padding:4px 8px;text-align:right}}
th{{background:#f3f3f3}}img{{max-width:100%;border:1px solid #eee;margin:8px 0}}code{{background:#f5f5f5;padding:1px 4px}}</style>
<h1>TiDB v8.5.8 단일 노드 vs 3노드 비교 (WSL 단일 호스트 시뮬레이션)</h1>
<p>노드 1대 = TiKV 3코어/4G + TiDB 2코어/2G + PD 1코어/1G (systemd CPUQuota/MemoryLimit). 데이터 sysbench 10테이블 × 10만 행.
prepare 소요: 단일 {prep.get('single')}s, 3노드 {prep.get('3node')}s.</p>
<img src="data:image/png;base64,{b64(figs['base'])}">
<img src="data:image/png;base64,{b64(figs['hotspot'])}">
<img src="data:image/png;base64,{b64(figs['scale'])}">
<img src="data:image/png;base64,{b64(figs['cpq'])}">
<img src="data:image/png;base64,{b64(figs['fault'])}">
{{FINDINGS}}<h2>원시 측정값</h2>
<table><tr><th>cluster</th><th>tag</th><th>workload</th><th>threads</th><th>TPS</th><th>QPS</th><th>avg ms</th><th>p95 ms</th><th>클러스터 코어</th><th>호스트 CPU%</th><th>throttle 합</th></tr>{table}</table>
</html>"""
html = html.replace("{FINDINGS}", open(f"{LAB}/scripts/findings.html").read())
open(f"{R}/report.html", "w").write(html)
print("OK", f"{R}/report.html", f"{R}/summary.png")
