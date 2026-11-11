#!/usr/bin/env bash
# 테이블별 레코드/인덱스(k_N)를 3개 리전으로 분할 + scatter 후 리더 분포 출력
# 사용: split.sh <tidb_port>
P=${1:-4001}
SQL="SET SESSION tidb_scatter_region='table';"
for i in $(seq 1 10); do
  SQL+="SPLIT TABLE sbtest.sbtest$i BETWEEN (0) AND (100001) REGIONS 3;"
  SQL+="SPLIT TABLE sbtest.sbtest$i INDEX k_$i BETWEEN (0) AND (100001) REGIONS 3;"
done
mysql -h127.0.0.1 -P$P -uroot -N -e "$SQL" | awk '{s+=$1} END {print "split 생성 리전 수:", s}'
sleep 20   # scatter/리더 이동 반영 대기
mysql -h127.0.0.1 -P$P -uroot -t -e "
SELECT p.store_id, SUM(NOT s.is_index) AS record_leaders, SUM(s.is_index) AS index_leaders, COUNT(*) AS total
FROM information_schema.tikv_region_status s
JOIN information_schema.tikv_region_peers p ON s.region_id=p.region_id AND p.is_leader=1
WHERE s.db_name='sbtest' GROUP BY p.store_id ORDER BY p.store_id;"
