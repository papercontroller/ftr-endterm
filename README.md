# Resilient University Information System

Endterm project for Fault Tolerance and Dependable Computing (AITU).
It continues the midterm project [midterm-ftr](https://github.com/papercontroller/midterm-ftr).
An unmodified copy of the midterm is in `baseline-midterm/` and is the baseline for all comparisons.

## Structure

| Path | Purpose |
|---|---|
| `cmd/gateway` | API gateway: JWT auth, RBAC, rate limit, health probes, failover |
| `cmd/student-service` | Student profiles, course registration |
| `cmd/payment-service` | Tuition payments with idempotency keys |
| `cmd/academic-service` | Transcripts, circuit breaker, last-known-good cache |
| `pkg/` | Shared logger, metrics, middleware, breaker and database connection |
| `config/` | Database schema, Prometheus, alert rules, Grafana dashboard |
| `k8s/` | Kubernetes manifests (not tested on a cluster) |
| `tests/`, `cmd/*/main_test.go` | Automated tests |
| `scripts/run_experiments.py` | All fault-injection and security experiments |
| `results/` | Measured results and raw request data |
| `baseline-midterm/` | Midterm at commit 871967a |

## Run

Needs Docker Desktop. Python 3 and Go are only needed for experiments and tests.

```bash
cp .env.example .env      # then replace the three values
docker compose up -d --build
```

| URL | What |
|---|---|
| http://127.0.0.1:8080 | API gateway |
| http://127.0.0.1:3000/d/uis-resilience | Grafana dashboard (anonymous view) |
| http://127.0.0.1:9090/alerts | Prometheus alerts |

Synthetic accounts are `student1`, `student2`, `registrar1` and `admin1`. All use `DEMO_PASSWORD` from `.env`.

```bash
TOKEN=$(curl -s 127.0.0.1:8080/auth/login \
  -d '{"username":"student1","password":"YOUR_DEMO_PASSWORD"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s 127.0.0.1:8080/api/transcripts/1 -H "Authorization: Bearer $TOKEN"
```

## Tests

```bash
go test ./...
```

## Experiments

```bash
python scripts/run_experiments.py                  # everything, about 40 minutes
python scripts/run_experiments.py --only E1 E2     # a subset
python scripts/run_experiments.py --target endterm --reps 1
python scripts/summarize_results.py                # print the means used in the report
python scripts/generate_diagrams.py                # redraw charts from results/
python scripts/capture_evidence.py                 # demo incident, Grafana screenshot, sample logs
```

The midterm and endterm both use port 8080, so the script runs one at a time and
switches automatically. It resets containers and tables before each repetition.

## Demo script

Open Grafana and a terminal side by side. Log in first, as shown above.

**1. Service failure.** Crash a replica while reading in a loop. All requests stay 200 and Docker restarts the replica.

```bash
while true; do curl -s -o /dev/null -w "%{http_code}\n" 127.0.0.1:8080/api/transcripts/1 -H "Authorization: Bearer $TOKEN"; sleep 0.2; done
docker exec academic-service-1 kill -TERM 1      # in a second terminal
docker logs api-gateway 2>&1 | grep "health changed" | tail -2
```

**2. Database freeze and graceful degradation.** Read the transcript twice or more first, so that both replicas cache it.

```bash
docker pause uis-postgres-db
curl -s 127.0.0.1:8080/api/transcripts/1 -H "Authorization: Bearer $TOKEN"   # status DEGRADED_CACHED_MODE
docker unpause uis-postgres-db
```

**3. Security.** Each request is denied and logged.

```bash
curl -i 127.0.0.1:8080/api/transcripts/1                                         # 401
curl -i 127.0.0.1:8080/api/transcripts/2 -H "Authorization: Bearer $TOKEN"       # 403, another student
docker logs api-gateway 2>&1 | grep AUDIT | tail -3
```

**4. Request tracing.** One ID appears in the logs of the gateway and of the replica that served it.

```bash
curl -s 127.0.0.1:8080/api/transcripts/1 -H "Authorization: Bearer $TOKEN" -H "X-Correlation-ID: demo-1"
docker logs api-gateway 2>&1 | grep demo-1
docker logs academic-service-1 2>&1 | grep demo-1; docker logs academic-service-2 2>&1 | grep demo-1
```

## Known limitations

- PostgreSQL is a single node, so it is a single point of failure.
- There is no TLS between client and gateway or between services.
- Login uses fixed synthetic accounts with one shared password.
- The transcript cache is per replica and is lost on restart.
- `/metrics` on the gateway is public.
- Docker does not restart a container that is only unhealthy. Kubernetes liveness probes would.
