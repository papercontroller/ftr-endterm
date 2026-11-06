"""Repeatable fault-injection and security experiments.

Every number in results/ comes from real requests against real containers. This
script only injects the fault, sends traffic and records what happened.

    python scripts/run_experiments.py                 all experiments, both systems
    python scripts/run_experiments.py --only E1 E2    a subset
    python scripts/run_experiments.py --target endterm --reps 1

The midterm baseline in baseline-midterm/ and the endterm system both use port
8080, so only one runs at a time. The script switches between them. It needs only
the Python standard library and Docker.
"""
import argparse
import csv
import datetime
import http.client
import json
import os
import platform
import re
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
RAW = os.path.join(RESULTS, "raw")
GATEWAY_HOST, GATEWAY_PORT = "127.0.0.1", 8080  # localhost can hit the WSL relay on ::1
PROM = "http://127.0.0.1:9090"
CLIENT_TIMEOUT = 5.0


def sh(cmd, check=False, cwd=ROOT, env=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd, env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stdout}\n{r.stderr}")
    return (r.stdout + r.stderr).strip()


def read_env():
    env = {}
    with open(os.path.join(ROOT, ".env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


class Target:
    """What differs between the two systems: URLs, container names, auth."""

    def __init__(self, name):
        self.name = name
        self.env = read_env()
        if name == "endterm":
            self.transcript = "/api/transcripts/1"
            self.pay = "/api/payments"
            self.victim = "academic-service-1"
            self.pay_victim = "payment-service-1"
            self.db = "uis-postgres-db"
            self.psql = "psql -U uis_user -d uis_db"
            self.cwd, self.up = ROOT, "docker compose up -d"
            self.down = "docker compose down"
        else:
            self.transcript = "/transcript"
            self.pay = "/pay"
            self.victim = "midterm-app-replica-1-1"
            self.pay_victim = "midterm-app-replica-1-1"
            self.db = "midterm-db-1"
            self.psql = "psql -U user -d uis"
            self.cwd = os.path.join(ROOT, "baseline-midterm")
            self.up, self.down = "docker compose -p midterm up -d", "docker compose -p midterm down -v"
        self.token = None

    def start(self, env=None, build=False):
        sh(self.up + (" --build" if build else ""), check=True, cwd=self.cwd, env=env)

    def stop(self):
        sh(self.down, cwd=self.cwd)

    def sql(self, q):
        return sh(f'docker exec {self.db} {self.psql} -Atc "{q}"')

    def login(self, user="student1"):
        if self.name == "midterm":
            return None
        status, body, _ = request("POST", "/auth/login", {"username": user, "password": self.env["DEMO_PASSWORD"]})
        return json.loads(body)["token"] if status == 200 else None

    def reset(self, env=None):
        """Known state before every repetition: all containers up, tables empty."""
        sh(f"docker unpause {self.db}")
        # After a pause the health status stays "unhealthy" for a few seconds and compose refuses to start.
        deadline = time.time() + 30
        while time.time() < deadline and sh(f'docker inspect -f "{{{{.State.Health.Status}}}}" {self.db}') == "unhealthy":
            time.sleep(0.5)
        self.start(env)
        self.sql("TRUNCATE payments, idempotency_keys RESTART IDENTITY")
        if self.name == "endterm":
            self.sql("TRUNCATE enrollments RESTART IDENTITY; UPDATE courses SET enrolled = 0")
        deadline = time.time() + 90
        path = "/readyz" if self.name == "endterm" else "/healthz"
        while time.time() < deadline:
            if request("GET", path)[0] == 200 and self.db_ok():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("system did not become ready")
        time.sleep(3 if self.name == "midterm" else 4)  # token bucket refill, replicas back in rotation
        self.token = self.login()

    def db_ok(self):
        return self.sql("SELECT 1") == "1"

    def fingerprint(self):
        """Hash of the records an attack must not change."""
        tables = "payments" if self.name == "midterm" else "payments, enrollments, transcripts, students, courses"
        out = []
        for t in tables.split(", "):
            out.append(t + "=" + self.sql(f"SELECT count(*), md5(coalesce(string_agg(x::text, ',' ORDER BY x::text), '')) FROM {t} x"))
        return out

    def crash(self, container):
        """Process exit from inside the container. A real crash, so the restart
        policy applies; `docker kill` counts as a manual stop and would not restart."""
        return sh(f"docker exec {container} kill -TERM 1")


def request(method, path, body=None, token=None, headers=None, raw=None, timeout=CLIENT_TIMEOUT, conn=None):
    """Returns (status, text, seconds). status 0 is a transport error or timeout."""
    h = dict(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    if token:
        h["Authorization"] = "Bearer " + token
    own = conn is None
    c = conn or http.client.HTTPConnection(GATEWAY_HOST, GATEWAY_PORT, timeout=timeout)
    start = time.perf_counter()
    try:
        c.request(method, path, data, h)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        return r.status, text, time.perf_counter() - start
    except Exception as e:
        c.close()
        return 0, type(e).__name__, time.perf_counter() - start
    finally:
        if own:
            c.close()


class Load:
    """Open-loop generator: sends at the planned rate, even if the system is slow."""

    def __init__(self, steps, make_request, workers=128):
        self.schedule = []
        t = 0.0
        for rate, seconds in steps:
            self.schedule += [t + i / rate for i in range(int(rate * seconds))]
            t += seconds
        self.make_request = make_request
        self.workers = workers
        self.samples = []
        self.lock = threading.Lock()
        self.next = 0
        self.t0 = None
        self.halted = threading.Event()

    def _worker(self):
        conn = http.client.HTTPConnection(GATEWAY_HOST, GATEWAY_PORT, timeout=CLIENT_TIMEOUT)
        while True:
            with self.lock:
                i = self.next
                self.next += 1
            if i >= len(self.schedule):
                conn.close()
                return
            planned = self.t0 + self.schedule[i]
            delay = planned - time.perf_counter()
            if delay > 0 and self.halted.wait(delay):
                conn.close()
                return
            method, path, body, token = self.make_request(i)
            sent = time.perf_counter()
            status, text, seconds = request(method, path, body, token, conn=conn)
            if status == 0:
                conn = http.client.HTTPConnection(GATEWAY_HOST, GATEWAY_PORT, timeout=CLIENT_TIMEOUT)
            with self.lock:
                self.samples.append((sent - self.t0, status, seconds, text))

    def start(self):
        self.t0 = time.perf_counter()
        self.threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.workers)]
        for t in self.threads:
            t.start()
        return self

    def now(self):
        return time.perf_counter() - self.t0

    def wait(self):
        for t in self.threads:
            t.join()
        self.samples.sort()
        return self.samples


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def summarize(samples, ok=lambda s: s[1] == 200):
    latencies = [s[2] * 1000 for s in samples]
    good = sum(1 for s in samples if ok(s))
    codes = {}
    for s in samples:
        codes[str(s[1])] = codes.get(str(s[1]), 0) + 1
    return {
        "total": len(samples), "success": good, "failed": len(samples) - good,
        "error_rate_pct": round(100 * (len(samples) - good) / len(samples), 2) if samples else None,
        "availability_pct": round(100 * good / len(samples), 2) if samples else None,
        "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "latency_p95_ms": round(percentile(latencies, 95), 1) if latencies else None,
        "latency_max_ms": round(max(latencies), 1) if latencies else None,
        "status_codes": codes,
    }


def save_samples(exp, target, rep, samples):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f"{exp}_{target}_rep{rep}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_sec", "status", "latency_ms", "body_prefix"])
        for t, status, seconds, text in samples:
            w.writerow([round(t, 3), status, round(seconds * 1000, 1), text[:60]])


def firing_alerts():
    try:
        url = PROM + "/api/v1/query?query=" + urllib.parse.quote('ALERTS{alertstate="firing"}')
        data = json.load(urllib.request.urlopen(url, timeout=2))
        return {m["metric"]["alertname"] for m in data["data"]["result"]}
    except Exception:
        return set()


def prom_value(query):
    try:
        data = json.load(urllib.request.urlopen(PROM + "/api/v1/query?query=" + urllib.parse.quote(query), timeout=2))
        result = data["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except Exception:
        return None


def wait_alerts_clear(limit=90):
    end = time.time() + limit
    while time.time() < end:
        if not firing_alerts():
            return True
        time.sleep(1)
    return False


class Watcher:
    """Polls a condition and records when it first turns true, on the runner's clock."""

    def __init__(self, conditions, interval=0.1):
        self.conditions = conditions
        self.first = {}
        self.interval = interval
        self.stop_flag = False
        self.start_at = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop_flag:
            for name, check in self.conditions.items():
                if name not in self.first and check():
                    self.first[name] = time.perf_counter()
            time.sleep(self.interval)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_flag = True
        self.thread.join()
        return self.first


def replica_health(replica):
    status, text, _ = request("GET", "/metrics", timeout=1)
    if status != 200:
        return None
    m = re.search(r'upstream_replica_healthy\{replica="%s",service="[^"]+"\} (\d)' % re.escape(replica), text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- experiments

def e1_instance_failure(tg, rep):
    """Crash one replica under steady read traffic."""
    tg.reset()
    tok = tg.token
    load = Load([(15, 30)], lambda i: ("GET", tg.transcript, None, tok)).start()
    time.sleep(5)
    conditions = {}
    if tg.name == "endterm":
        conditions = {"removed": lambda: replica_health("academic-service-1:8083") == 0}
    w = Watcher(conditions).start()
    injected = time.perf_counter()
    crash_output = tg.crash(tg.victim)
    if tg.name == "endterm":
        while "removed" not in w.first and time.perf_counter() - injected < 10:
            time.sleep(0.05)
        w.conditions["rejoined"] = lambda: "removed" in w.first and replica_health("academic-service-1:8083") == 1
    samples = load.wait()
    first = w.stop()
    save_samples("E1", tg.name, rep, samples)
    state = sh(f'docker inspect -f "{{{{.State.Status}}}} restarts={{{{.RestartCount}}}}" {tg.victim}')
    return {
        **summarize(samples),
        "crash_output": crash_output,
        "failure_window": summarize([s for s in samples if 5 <= s[0] <= 8]),
        "detection_time_sec": round(first["removed"] - injected, 2) if "removed" in first else None,
        "recovery_time_sec": round(first["rejoined"] - injected, 2) if "rejoined" in first else None,
        "victim_state_after": state,
        "alerts": sorted(firing_alerts()) if tg.name == "endterm" else None,
    }


def e2_dependency_block(tg, rep):
    """Freeze the database for 15 s with docker pause; TCP stays up, nothing answers."""
    tg.reset()
    tok = tg.token
    for _ in range(12):  # both replicas must have the record cached
        request("GET", tg.transcript, token=tok)
    load = Load([(15, 35)], lambda i: ("GET", tg.transcript, None, tok)).start()
    time.sleep(5)
    injected = load.now()
    sh(f"docker pause {tg.db}")
    time.sleep(7)
    breaker_mid = prom_value("max(circuit_breaker_state)") if tg.name == "endterm" else None
    pay_status, pay_text, pay_seconds = request(
        "POST", tg.pay, {"idempotency_key": f"e2-{rep}", "student_id": 1, "amount": 5.0}, tok)
    time.sleep(max(0, 15 - (load.now() - injected)))
    resumed = load.now()
    sh(f"docker unpause {tg.db}")
    samples = load.wait()
    save_samples("E2", tg.name, rep, samples)

    def is_degraded(s):
        return "DEGRADED" in s[3] or "degraded" in s[3]

    during = [s for s in samples if injected <= s[0] <= resumed]
    degraded = [s for s in during if is_degraded(s)]
    fresh_after = [s for s in samples if s[0] > resumed and s[1] == 200 and not is_degraded(s)]
    # recovery = the moment after which every response is fresh again
    last_bad = max([s[0] + s[2] for s in samples if s[0] > injected and (s[1] != 200 or is_degraded(s))], default=None)
    return {
        **summarize(samples),
        "during_fault": summarize(during),
        "degraded_responses": len(degraded),
        "real_data_during_fault_pct": round(100 * sum(1 for s in during if '"gpa"' in s[3]) / len(during), 2) if during else None,
        "first_degraded_after_sec": round(degraded[0][0] - injected, 2) if degraded else None,
        "first_fresh_after_resume_sec": round(fresh_after[0][0] - resumed, 2) if fresh_after else None,
        "recovery_after_resume_sec": round(last_bad - resumed, 2) if last_bad else None,
        "breaker_state_during_fault": breaker_mid,
        "payment_during_fault": {"status": pay_status, "seconds": round(pay_seconds, 2)},
        "payments_in_db_after": tg.sql("SELECT count(*) FROM payments"),
        "degraded_sample": degraded[0][3][:200] if degraded else None,
    }


def e3_load_spike(tg, rep):
    """Stepped spike. The rate limiter is raised because all synthetic clients share
    one IP; the aim here is to measure capacity, not the limiter (see E6)."""
    env = {"RATE_LIMIT_RPS": "100000", "RATE_LIMIT_BURST": "100000"}
    tg.reset(env)
    tok = tg.token

    def make(i):
        if i % 10 == 0:
            return "POST", tg.pay, {"idempotency_key": f"e3-{rep}-{i}", "student_id": 1, "amount": 1.0}, tok
        return "GET", tg.transcript, None, tok

    load = Load([(20, 10), (300, 20), (20, 10)], make, workers=200).start()
    time.sleep(20)
    stats = sh('docker stats --no-stream --format "{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}"')
    samples = load.wait()
    save_samples("E3", tg.name, rep, samples)
    ok = lambda s: s[1] in (200, 201)
    spike = [s for s in samples if 10 <= s[0] < 30]
    posts_ok = sum(1 for i, s in enumerate(samples) if s[1] == 201 or '"SUCCESS"' in s[3])
    prefix = "uis|academic|payment|student|api-gateway" if tg.name == "endterm" else "midterm"
    return {
        **summarize(samples, ok),
        "spike": {**summarize(spike, ok), "achieved_rps": round(len(spike) / 20, 1)},
        "payments_acknowledged": posts_ok,
        "payments_in_db": tg.sql("SELECT count(*) FROM payments"),
        "duplicate_payments": tg.sql("SELECT count(*) - count(DISTINCT id) FROM payments"),
        "resources_during_spike": [line for line in stats.splitlines() if re.match(prefix, line)],
    }


def e4_alerts(tg, rep):
    """One deliberate incident per alert rule, plus a quiet period as negative control."""
    tok_holder = {}

    def scenario(name, expected, inject, restore, make_request, rate, limit=50):
        tg.reset()
        wait_alerts_clear()
        tok_holder["t"] = tg.token
        load = Load([(rate, limit)], make_request).start()
        time.sleep(3)
        injected = time.perf_counter()
        inject()
        fired, seen = None, set()
        while time.perf_counter() - injected < limit - 5:
            now = firing_alerts()
            seen |= now
            if expected in now:
                fired = time.perf_counter() - injected
                break
            time.sleep(0.25)
        restore()
        load.halted.set()
        samples = load.wait()
        save_samples(f"E4-{name}", tg.name, rep, samples)
        time.sleep(1)
        seen |= firing_alerts()
        return {"scenario": name, "expected_alert": expected, "fired": fired is not None,
                "detection_delay_sec": round(fired, 2) if fired else None,
                "all_alerts_seen": sorted(seen), "extra_alerts": sorted(seen - {expected})}

    tg.reset()
    wait_alerts_clear()
    tok = tg.token
    quiet = Load([(5, 60)], lambda i: ("GET", tg.transcript, None, tok)).start()
    seen = set()
    while any(t.is_alive() for t in quiet.threads):
        seen |= firing_alerts()
        time.sleep(1)
    quiet.wait()

    results = [{"scenario": "quiet_period", "expected_alert": None, "fired": bool(seen),
                "detection_delay_sec": None, "all_alerts_seen": sorted(seen), "extra_alerts": sorted(seen)}]
    results.append(scenario(
        "instance_down", "ServiceInstanceDown",
        lambda: sh("docker stop student-service-2"), lambda: sh("docker start student-service-2"),
        lambda i: ("GET", "/api/courses", None, tok_holder["t"]), 5))
    results.append(scenario(
        "breaker_open", "CircuitBreakerOpen",
        lambda: sh(f"docker pause {tg.db}"), lambda: sh(f"docker unpause {tg.db}"),
        lambda i: ("GET", tg.transcript, None, tok_holder["t"]), 10))
    results.append(scenario(
        "error_rate", "HighErrorRate",
        lambda: sh("docker stop payment-service-1 payment-service-2"),
        lambda: sh("docker start payment-service-1 payment-service-2"),
        lambda i: ("POST", tg.pay, {"idempotency_key": f"e4-{rep}-{i}", "student_id": 1, "amount": 1.0}, tok_holder["t"]), 5))
    results.append(scenario(
        "latency", "HighRequestLatencyP95",
        lambda: sh(f"docker pause {tg.db}"), lambda: sh(f"docker unpause {tg.db}"),
        lambda i: ("GET", "/api/courses", None, tok_holder["t"]), 5))
    results.append(scenario(
        "abuse", "AbuseRateLimitTriggered",
        lambda: None, lambda: None,
        lambda i: ("GET", "/api/courses", None, tok_holder["t"]), 45))
    sh(f"docker unpause {tg.db}")
    return {"scenarios": results}


def forged_tokens(tok):
    import base64
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    none_token = enc(b'{"alg":"none","typ":"JWT"}') + "." + enc(b'{"user_id":"1","role":"Admin","exp":4102444800}') + "."
    head, payload, sig = tok.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    claims["role"] = "Admin"
    role_swapped = f"{head}.{enc(json.dumps(claims).encode())}.{sig}"
    return none_token, role_swapped


def e5_unauthorized(tg, rep):
    """Protected operations without valid authorization."""
    tg.reset()
    before = tg.fingerprint()
    cases = []

    def case(name, method, path, expected, body=None, token=None, headers=None, secret=None):
        status, text, _ = request(method, path, body, token, headers)
        leaked = bool(secret and secret in text)
        cases.append({"case": name, "status": status, "expected": expected, "denied": status in (401, 403),
                      "passed": status == expected and not leaked, "data_exposed": leaked})

    if tg.name == "endterm":
        tok = tg.token
        none_token, role_swapped = forged_tokens(tok)
        pay = {"idempotency_key": f"e5-{rep}", "student_id": 1, "amount": 99.0}
        audit_before = prom_counter_sum("audit_security_events_total", gateway=True)
        case("transcript without token", "GET", "/api/transcripts/1", 401, secret="gpa")
        case("payment without token", "POST", "/api/payments", 401, body=pay)
        case("tampered signature", "GET", "/api/transcripts/1", 401, token=tok[:-3] + "abc", secret="gpa")
        case("alg=none forged Admin token", "GET", "/api/students", 401, token=none_token, secret="email")
        case("role changed inside token", "GET", "/api/students", 401, token=role_swapped, secret="email")
        case("student lists all students", "GET", "/api/students", 403, token=tok, secret="email")
        case("student reads other transcript", "GET", "/api/transcripts/2", 403, token=tok, secret="gpa")
        case("student reads other profile", "GET", "/api/students/2", 403, token=tok, secret="email")
        case("student pays for other student", "POST", "/api/payments", 403, token=tok,
             body={"idempotency_key": f"e5b-{rep}", "student_id": 2, "amount": 99.0})
        case("forged identity headers", "GET", "/api/transcripts/2", 403, token=tok,
             headers={"X-User-ID": "2", "X-User-Role": "Admin"}, secret="gpa")
        status, body, _ = request("POST", "/auth/login", {"username": "nobody", "password": "wrong"})
        cases.append({"case": "login with bad credentials", "status": status, "expected": 401, "denied": True,
                      "passed": status == 401, "data_exposed": False})
        status, body, _ = request("POST", "/auth/login", {"username": "student1", "password": tg.env["DEMO_PASSWORD"], "role": "Admin"})
        escalated = '"role":"Admin"' in body
        cases.append({"case": "client requests Admin role at login", "status": status, "expected": 200, "denied": not escalated,
                      "passed": not escalated, "data_exposed": False})
        time.sleep(0.2)
        audit_events = prom_counter_sum("audit_security_events_total", gateway=True) - audit_before
        logs = sh("docker logs --since 30s api-gateway")
        with open(os.path.join(RAW, f"E5_audit_rep{rep}.log"), "w", encoding="utf-8") as f:
            f.writelines(line + "\n" for line in logs.splitlines() if '"AUDIT"' in line)
    else:
        case("transcript without token", "GET", tg.transcript, 401, secret="gpa")
        case("payment without token", "POST", tg.pay, 401,
             body={"idempotency_key": f"e5-{rep}", "student_id": 1, "amount": 99.0})
        case("server-side request forgery via /external", "GET",
             "/external?url=http://app-replica-2:8080/healthz", 403, secret="server_id")
        audit_events = None

    after = tg.fingerprint()
    return {"cases": cases, "denied": sum(c["passed"] for c in cases), "total": len(cases),
            "data_unchanged": before == after, "audit_events_gateway": audit_events}


def prom_counter_sum(name, gateway=False):
    status, text, _ = request("GET", "/metrics", timeout=2)
    return sum(float(m) for m in re.findall(r"^%s\{[^}]*\} ([0-9.e+]+)$" % name, text, re.M)) if status == 200 else 0


def e6_abuse(tg, rep):
    """Malformed input, then a burst, then proof that normal users still get through."""
    tg.reset()
    tok = tg.token
    before = tg.fingerprint()
    pay = tg.pay
    cases = []

    def bad(name, method, path, raw=None, body=None, headers=None):
        status, text, _ = request(method, path, body, tok, headers, raw=raw)
        cases.append({"case": name, "status": status, "rejected": 400 <= status < 500, "server_error": status >= 500 or status == 0})

    base = {"idempotency_key": f"e6-{rep}", "student_id": 1, "amount": 10.0}
    bad("broken JSON", "POST", pay, raw=b'{"amount": ')
    bad("negative amount", "POST", pay, body={**base, "amount": -100})
    bad("zero amount", "POST", pay, body={**base, "amount": 0})
    bad("amount too large", "POST", pay, body={**base, "amount": 1e12})
    bad("missing idempotency key", "POST", pay, body={"student_id": 1, "amount": 10})
    bad("idempotency key 300 chars", "POST", pay, body={**base, "idempotency_key": "k" * 300})
    bad("SQL injection in key field", "POST", pay, body={**base, "idempotency_key": "x'; DROP TABLE payments;--", "amount": "1 OR 1=1"})
    bad("body of 2 MB", "POST", pay, raw=b'{"pad":"' + b"a" * (2 << 20) + b'"}')
    if tg.name == "endterm":
        bad("wrong type for student_id", "POST", pay, body={**base, "student_id": "1 OR 1=1"})
        bad("unknown currency format", "POST", pay, body={**base, "currency": "'; DROP--"})
        bad("SQL injection in course id", "POST", "/api/students/1/courses", body={"course_id": "x' OR '1'='1"})
        bad("non-numeric transcript id", "GET", "/api/transcripts/1%27%20OR%201=1")
    time.sleep(4)

    # Burst from one client. 300 requests as fast as 32 connections can send them.
    read_path = "/api/courses" if tg.name == "endterm" else tg.transcript
    spoof = Load([(1000, 0.3)], lambda i: ("GET", read_path, None, tok), workers=32).start().wait()
    burst = summarize(spoof)
    time.sleep(4)
    # The same burst with a new X-Forwarded-For per request tries to escape the limiter.
    results = []
    lock = threading.Lock()

    def spoofed(start):
        conn = http.client.HTTPConnection(GATEWAY_HOST, GATEWAY_PORT, timeout=CLIENT_TIMEOUT)
        for i in range(25):
            s = request("GET", read_path, None, tok, {"X-Forwarded-For": f"10.9.{start}.{i}"}, conn=conn)
            with lock:
                results.append((0, s[0], s[2], s[1]))
        conn.close()

    threads = [threading.Thread(target=spoofed, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spoofed_summary = summarize(results)
    # Recovery of normal service
    time.sleep(3)
    good, _, seconds = request("GET", read_path, None, tok)
    health = request("GET", "/healthz")[0]
    after = tg.fingerprint()
    stability = sh('docker ps --format "{{.Names}} {{.Status}}"')
    prefix = "api-gateway|academic|payment|student|uis-postgres" if tg.name == "endterm" else "midterm"
    return {
        "malformed": cases,
        "malformed_rejected": sum(c["rejected"] for c in cases), "malformed_total": len(cases),
        "server_errors": sum(c["server_error"] for c in cases),
        "burst": burst, "burst_with_spoofed_xff": spoofed_summary,
        "normal_request_after": good, "normal_latency_ms": round(seconds * 1000, 1), "health_after": health,
        "data_unchanged": before == after,
        "containers": [line for line in stability.splitlines() if re.match(prefix, line)],
    }


def e7_recovery_consistency(tg, rep, payments=300, workers=8):
    """Crash a payment instance during a batch. Every 4th payment is sent twice at the
    same moment, as a client would on a lost response. Then count what is in the database.
    The limiter is raised as in E3: the midterm has none, so 429s would hide the crash."""
    tg.reset({"RATE_LIMIT_RPS": "100000", "RATE_LIMIT_BURST": "100000"})
    tok = tg.token
    acked = {}
    attempts = {"total": 0, "failed": 0, "server_500": 0}
    failure_codes = {}
    lock = threading.Lock()
    index = {"next": 0}

    def send(key, amount):
        body = {"idempotency_key": key, "student_id": 1, "amount": amount}
        backoff = 0.2
        for _ in range(10):
            status, text, _ = request("POST", tg.pay, body, tok)
            with lock:
                attempts["total"] += 1
                if status not in (200, 201):
                    attempts["failed"] += 1
                    failure_codes[str(status)] = failure_codes.get(str(status), 0) + 1
                if status == 500:
                    attempts["server_500"] += 1
            if status in (200, 201):
                return True
            time.sleep(backoff)
            backoff = min(backoff * 2, 2)
        return False

    def worker():
        while True:
            with lock:
                i = index["next"]
                index["next"] += 1
            if i >= payments:
                return
            key, amount = f"e7-{rep}-{i}", round(1000 + i / 100, 2)
            if i % 4 == 0:
                twin = threading.Thread(target=send, args=(key, amount))
                twin.start()
                ok = send(key, amount)
                twin.join()
            else:
                ok = send(key, amount)
            if ok:
                with lock:
                    acked[key] = amount
            time.sleep(0.02)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    while index["next"] < payments / 3:
        time.sleep(0.01)
    tg.crash(tg.pay_victim)
    crashed_at = time.perf_counter() - start
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start
    time.sleep(1)
    rows = tg.sql("SELECT amount, count(*) FROM payments GROUP BY amount").splitlines()
    in_db = {float(r.split("|")[0]): int(r.split("|")[1]) for r in rows if "|" in r}
    return {
        "payments_planned": payments, "acknowledged": len(acked), "rows_in_db": sum(in_db.values()),
        "distinct_in_db": len(in_db),
        "duplicate_rows": sum(n - 1 for n in in_db.values() if n > 1),
        "lost_acknowledged": sum(1 for v in acked.values() if v not in in_db),
        "not_acknowledged_but_stored": sum(1 for v in in_db if v not in set(acked.values())),
        "http_attempts": attempts["total"], "failed_attempts": attempts["failed"], "server_500": attempts["server_500"],
        "failure_codes": failure_codes,
        "crash_at_sec": round(crashed_at, 2), "duration_sec": round(elapsed, 2),
        "orphan_payments_without_key": int(tg.sql("SELECT (SELECT count(*) FROM payments) - (SELECT count(*) FROM idempotency_keys)") or 0),
        "victim_state_after": sh(f'docker inspect -f "{{{{.State.Status}}}} restarts={{{{.RestartCount}}}}" {tg.pay_victim}'),
    }


EXPERIMENTS = {
    "E1": ("Service instance failure", e1_instance_failure, 5, ("midterm", "endterm")),
    "E2": ("Dependency timeout (database frozen)", e2_dependency_block, 3, ("midterm", "endterm")),
    "E3": ("Load spike", e3_load_spike, 3, ("midterm", "endterm")),
    "E4": ("Monitoring and alert test", e4_alerts, 3, ("endterm",)),
    "E5": ("Unauthorized access", e5_unauthorized, 3, ("midterm", "endterm")),
    "E6": ("Abusive and malformed requests", e6_abuse, 3, ("midterm", "endterm")),
    "E7": ("Recovery and consistency", e7_recovery_consistency, 5, ("midterm", "endterm")),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=list(EXPERIMENTS))
    ap.add_argument("--target", choices=["midterm", "endterm"])
    ap.add_argument("--reps", type=int, help="override the repetition count")
    args = ap.parse_args()

    os.makedirs(RAW, exist_ok=True)
    out_path = os.path.join(RESULTS, "experiment_results.json")
    output = {"experiments": {}}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            output = json.load(f)
    output["environment"] = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"), "host_os": platform.platform(),
        "cpu_count": os.cpu_count(), "docker": sh('docker version --format "{{.Server.Version}}"'),
        "note": "All services, the database and the load generator share one Windows host running Docker Desktop.",
        "baseline_commit": "871967a (github.com/papercontroller/midterm-ftr)",
    }

    for target_name in ("midterm", "endterm"):
        if args.target and args.target != target_name:
            continue
        chosen = [e for e in args.only if target_name in EXPERIMENTS[e][3]]
        if not chosen:
            continue
        tg = Target(target_name)
        other = Target("endterm" if target_name == "midterm" else "midterm")
        other.stop()
        tg.start(build=True)
        for exp in chosen:
            title, fn, reps, _ = EXPERIMENTS[exp]
            reps = args.reps or reps
            print(f"== {exp} {title} / {target_name} / {reps} reps", flush=True)
            runs = []
            for rep in range(1, reps + 1):
                t0 = time.time()
                r = fn(tg, rep)
                r["rep"] = rep
                runs.append(r)
                print(f"  rep {rep}: {round(time.time() - t0)}s {json.dumps(r, ensure_ascii=False)[:420]}", flush=True)
            entry = output["experiments"].setdefault(exp, {"title": title, "targets": {}})
            entry["targets"][target_name] = {"repetitions": reps, "runs": runs}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
        tg.stop()
    Target("endterm").start()
    print("results written to", out_path)


if __name__ == "__main__":
    main()
