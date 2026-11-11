#!/usr/bin/env python3
# 장애 내성 테스트: sysbench 실행 중 TiKV / PD 리더를 SIGKILL 로 죽였다 살리며 2초 단위 TPS 타임라인 기록
# 사용: fault.py single | fault.py 3node
import json, re, subprocess, sys, threading, time, os, shutil, urllib.request
from pathlib import Path

SYSBENCH = os.environ.get("SYSBENCH") or shutil.which("sysbench") or "sysbench"
LAB = str(Path(__file__).resolve().parents[1])          # tidb-cluster/

PLANS = {
    # 단일 노드: TiKV 하나가 죽으면 전체가 멈추는지 확인
    "single": {"ports": "4000", "pd": [2379], "total": 80,
               "events": [(20, "kill", "tikv-20160"), (45, "start", "tikv-20160")]},
    # 3노드: node3 TiKV 크래시 -> 복구, 이후 PD 리더 크래시 -> 복구
    "3node": {"ports": "4001,4002,4003", "pd": [2381, 2383, 2385], "total": 170,
              "events": [(30, "kill", "tikv-20163"), (75, "start", "tikv-20163"),
                         (110, "kill", "PD_LEADER"), (140, "start", "PD_LEADER")]},
}

def pd_leader_unit(ports):
    # PD API 로 현재 PD 리더의 client 포트를 찾아 systemd 유닛명으로 변환
    for p in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/pd/api/v1/leader", timeout=2) as r:
                url = json.load(r)["client_urls"][0]
                return "pd-" + url.rsplit(":", 1)[1]
        except Exception:
            continue

def store_snapshot(ports):
    # 스토어별 상태/리더 수 스냅샷 (살아있는 PD 아무거나)
    for p in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/pd/api/v1/stores", timeout=2) as r:
                d = json.load(r)
            return {s["store"]["address"]: (s["store"]["state_name"], s["status"].get("leader_count"))
                    for s in d["stores"]}
        except Exception:
            continue

def main(name):
    plan = PLANS[name]
    log, resolved = [], {}
    cmd = [SYSBENCH, "oltp_read_write", "--mysql-host=127.0.0.1", f"--mysql-port={plan['ports']}",
           "--mysql-user=root", "--mysql-db=sbtest", "--mysql-ssl=off", "--tables=10",
           "--table_size=100000", "--threads=32", f"--time={plan['total']}",
           "--report-interval=2", "--mysql-ignore-errors=all", "--rand-type=uniform", "run"]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def injector():
        for at, action, target in plan["events"]:
            time.sleep(max(0, at - (time.time() - t0)))
            unit = target
            if target == "PD_LEADER":
                unit = resolved.setdefault("PD_LEADER", pd_leader_unit(plan["pd"]))
            if action == "kill":
                # 크래시 재현: SIGKILL 후 systemd 자동 재시작(Restart=always)을 막기 위해 stop
                subprocess.run(["systemctl", "kill", "--signal=SIGKILL", f"{unit}.service"])
                subprocess.run(["systemctl", "stop", f"{unit}.service"])
            else:
                subprocess.run(["systemctl", "start", f"{unit}.service"])
            log.append({"t": round(time.time() - t0, 1), "action": action, "unit": unit})
            print(f"  >>> t={log[-1]['t']}s {action} {unit}", flush=True)
    th = threading.Thread(target=injector); th.start()

    timeline = []
    pat = re.compile(r"\[\s*(\d+)s \].*?tps: ([\d.]+) qps: ([\d.]+).*?lat \(ms,95%\): ([\d.]+) err/s: ([\d.]+) reconn/s: ([\d.]+)")
    tail = []
    for line in proc.stdout:
        tail.append(line)
        if m := pat.search(line):
            timeline.append({"t": int(m[1]), "tps": float(m[2]), "qps": float(m[3]),
                             "p95": float(m[4]), "err": float(m[5]), "reconn": float(m[6])})
            print(line.rstrip(), flush=True)
    proc.wait(); th.join()
    # 장애 후 리더 재배치 상태 확인 (재기동 직후 + 30초 뒤)
    after = store_snapshot(plan["pd"]); time.sleep(30); after30 = store_snapshot(plan["pd"])
    res = {"cluster": name, "events": log, "timeline": timeline, "stores_after": after,
           "stores_after_30s": after30, "summary_tail": "".join(tail[-30:])}
    json.dump(res, open(f"{LAB}/results/fault-{name}.json", "w"), ensure_ascii=False, indent=1)
    print("stores_after:", after); print("stores_after_30s:", after30)

if __name__ == "__main__":
    main(sys.argv[1])
