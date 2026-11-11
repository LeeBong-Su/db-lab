#!/usr/bin/env python3
# TiDB 단일/3노드 비교용 sysbench 드라이버
# - 워크로드별 TPS/QPS/지연/에러 + 컴포넌트(systemd 유닛)별 CPU 사용 코어수/스로틀링 수집
# - 결과는 results/bench.jsonl 에 1줄=1런으로 누적
import json, re, subprocess, sys, time, os, shutil
from pathlib import Path

# sysbench 경로: 환경변수 SYSBENCH > PATH 순으로 찾는다
SYSBENCH = os.environ.get("SYSBENCH") or shutil.which("sysbench") or "sysbench"
LAB = str(Path(__file__).resolve().parents[1])          # tidb-cluster/
OUT = os.environ.get("BENCH_OUT", f"{LAB}/results/bench.jsonl")
RAW = os.environ.get("BENCH_RAW", f"{LAB}/results/raw")

# 클러스터별 접속 포트와 측정 대상 systemd 유닛
CLUSTERS = {
    "single": {"ports": "4000",
               "units": ["pd-2379", "tikv-20160", "tidb-4000"]},
    "3node":  {"ports": "4001,4002,4003",
               "units": ["pd-2381", "pd-2383", "pd-2385",
                         "tikv-20161", "tikv-20162", "tikv-20163",
                         "tidb-4001", "tidb-4002", "tidb-4003"]},
}
# 3노드 클러스터를 TiDB 1대(4001)로만 접속 - TiDB 자원을 단일과 맞춰 Raft 복제 비용만 분리
CLUSTERS["3node-1tidb"] = {"ports": "4001", "units": CLUSTERS["3node"]["units"]}
TABLES, TABLE_SIZE = 10, 100000

def base_args(ports):
    # 공통 접속 옵션 (사내망 이슈 회피용 SSL off - tidb-benchmarking 스킬 권고)
    return [f"--mysql-host=127.0.0.1", f"--mysql-port={ports}", "--mysql-user=root",
            "--mysql-db=sbtest", "--mysql-ssl=off",
            f"--tables={TABLES}", f"--table_size={TABLE_SIZE}"]

def unit_usage_usec(unit):
    # cgroup v2(unified) cpu.stat 의 누적 CPU 사용 시간(us)
    with open(f"/sys/fs/cgroup/unified/system.slice/{unit}.service/cpu.stat") as f:
        return int(f.readline().split()[1])

def unit_throttled(unit):
    # cgroup v1 cpu 컨트롤러의 스로틀링 횟수 (CPUQuota 에 걸린 횟수)
    d = {}
    with open(f"/sys/fs/cgroup/cpu/system.slice/{unit}.service/cpu.stat") as f:
        for line in f:
            k, v = line.split(); d[k] = int(v)
    return d["nr_throttled"]

def host_cpu():
    # /proc/stat 기준 호스트 전체 (busy, total) jiffies
    v = list(map(int, open("/proc/stat").readline().split()[1:]))
    idle = v[3] + v[4]
    return sum(v) - idle, sum(v)

def parse(out):
    # sysbench 결과 텍스트에서 핵심 지표 추출
    g = lambda pat: (float(m.group(1)) if (m := re.search(pat, out)) else None)
    return {
        "tps": g(r"transactions:\s+\d+\s+\(([\d.]+) per sec"),
        "qps": g(r"queries:\s+\d+\s+\(([\d.]+) per sec"),
        "errors": g(r"ignored errors:\s+(\d+)"),
        "lat_avg_ms": g(r"avg:\s+([\d.]+)"),
        "lat_p95_ms": g(r"95th percentile:\s+([\d.]+)"),
        "lat_max_ms": g(r"max:\s+([\d.]+)"),
    }

def run(cluster, workload, threads, secs, record=True, tag="base"):
    c = CLUSTERS[cluster]
    cmd = [SYSBENCH, workload, *base_args(c["ports"]), f"--threads={threads}",
           f"--time={secs}", "--report-interval=10", "--rand-type=uniform", "run"]
    u0 = {u: unit_usage_usec(u) for u in c["units"]}
    t0 = {u: unit_throttled(u) for u in c["units"]}
    h0 = host_cpu(); w0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    el = time.time() - w0; h1 = host_cpu()
    usage = {u: round((unit_usage_usec(u) - u0[u]) / 1e6 / el, 2) for u in c["units"]}
    thr = {u: unit_throttled(u) - t0[u] for u in c["units"]}
    r = {"tag": tag, "cluster": cluster, "workload": workload, "threads": threads, "secs": secs,
         **parse(p.stdout),
         "host_cpu_pct": round(100 * (h1[0] - h0[0]) / (h1[1] - h0[1]), 1),
         "cores_by_unit": usage, "throttled_by_unit": thr,
         "cores_total": round(sum(usage.values()), 2), "rc": p.returncode}
    if p.returncode != 0:
        r["stderr"] = (p.stderr or p.stdout)[-800:]
    if record:
        with open(OUT, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(f"{RAW}/raw-{cluster}-{tag}-{workload}-t{threads}.txt", "w") as f:
            f.write(p.stdout + p.stderr)
    print(json.dumps({k: r[k] for k in ("cluster", "workload", "threads", "tps", "qps",
          "lat_avg_ms", "lat_p95_ms", "cores_total", "host_cpu_pct", "rc")}, ensure_ascii=False), flush=True)
    return r

def prepare(cluster):
    c = CLUSTERS[cluster]
    port = c["ports"].split(",")[0]
    subprocess.run(["mysql", "-h127.0.0.1", f"-P{port}", "-uroot", "-e",
                    "DROP DATABASE IF EXISTS sbtest; CREATE DATABASE sbtest"], check=True)
    w0 = time.time()
    p = subprocess.run([SYSBENCH, "oltp_read_write", *base_args(c["ports"]),
                        f"--threads={TABLES}", "prepare"], capture_output=True, text=True)
    el = round(time.time() - w0, 1)
    print(f"[{cluster}] prepare rc={p.returncode} {el}s", flush=True)
    if p.returncode != 0:
        print(p.stdout[-1500:], p.stderr[-1500:]); sys.exit(1)
    with open(OUT, "a") as f:
        f.write(json.dumps({"cluster": cluster, "workload": "prepare",
                            "secs": el, "rows": TABLES * TABLE_SIZE}) + "\n")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cluster")
    ap.add_argument("threads", nargs="?", type=int, default=32)
    ap.add_argument("secs", nargs="?", type=int, default=60)
    ap.add_argument("--no-prepare", action="store_true")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--tag", default="base")           # 실험 단계 라벨 (base / presplit / split)
    ap.add_argument("--threads-list", default=None)    # 예: 32,128 (스레드 스윕)
    ap.add_argument("--workloads", default="oltp_point_select,oltp_read_only,oltp_write_only,oltp_read_write")
    a = ap.parse_args()
    if not a.no_prepare:
        prepare(a.cluster)
    if not a.no_warmup:
        run(a.cluster, "oltp_read_only", a.threads, 30, record=False)   # 워밍업 (기록 안 함)
    tl = [int(x) for x in a.threads_list.split(",")] if a.threads_list else [a.threads]
    for th in tl:
        for wl in a.workloads.split(","):
            run(a.cluster, wl, th, a.secs, record=True, tag=a.tag)
