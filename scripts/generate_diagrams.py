import json
import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches

os.makedirs("diagrams", exist_ok=True)

def generate_architecture_diagram():
    fig, ax = plt.subplots(figsize=(14, 9), dpi=300)
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 10)
    ax.axis('off')

    # Background color & styling
    fig.patch.set_facecolor('#F8F9FA')

    # Title
    ax.text(7, 9.6, "Resilient Cloud-Native University Information System Architecture", 
            ha='center', va='center', fontsize=16, fontweight='bold', color='#1A202C')
    ax.text(7, 9.2, "Student: Maulkan Sanzhar (CSE-2501M) | Course: Fault Tolerance & Dependable Computing (AITU)", 
            ha='center', va='center', fontsize=10, style='italic', color='#4A5568')

    # Tier 1: Clients & External Users
    ax.add_patch(patches.FancyBboxPatch((0.5, 7.2), 3.0, 1.4, boxstyle="round,pad=0.2", fc="#E2E8F0", ec="#4A5568", lw=1.5))
    ax.text(2.0, 8.2, "Client Tier (Web / Mobile)", ha='center', va='center', fontsize=11, fontweight='bold', color='#2D3748')
    ax.text(2.0, 7.6, "• Student Portal\n• Registrar Console\n• Admin Dashboard", ha='center', va='center', fontsize=8.5, color='#4A5568')

    # Arrow from Clients to Gateway
    ax.annotate("", xy=(4.2, 7.9), xytext=(3.5, 7.9),
                arrowprops=dict(arrowstyle="->", lw=2, color="#2B6CB0"))
    ax.text(3.85, 8.1, "HTTP / JSON", ha='center', va='center', fontsize=8, color="#2B6CB0", fontweight='bold')

    # Tier 2: Edge & Security Gateway Tier
    ax.add_patch(patches.FancyBboxPatch((4.2, 6.0), 4.2, 2.7, boxstyle="round,pad=0.25", fc="#EBF8FF", ec="#3182CE", lw=2))
    ax.text(6.3, 8.4, "Edge & Security Gateway (Port 8080)", ha='center', va='center', fontsize=12, fontweight='bold', color='#2B6CB0')
    ax.text(6.3, 7.8, "• Token Bucket Rate Limiter (50 burst, 20/s)\n• JWT Authentication & RBAC Middleware\n• Distributed Correlation ID Injection (X-Correlation-ID)\n• Round-Robin with Active Probes (1s) & Failover\n• Bounded Retries, Upstream Timeout (3s)\n• Security Audit Logger", 
            ha='center', va='center', fontsize=8.5, color='#2D3748')

    # Tier 3: Microservices Tier (Compute)
    # Service 1: Student Service
    ax.add_patch(patches.FancyBboxPatch((0.5, 2.5), 3.8, 2.6, boxstyle="round,pad=0.2", fc="#F0FFF4", ec="#38A169", lw=1.8))
    ax.text(2.4, 4.8, "Student Service (Port 8081)", ha='center', va='center', fontsize=11, fontweight='bold', color='#22543D')
    ax.text(2.4, 3.7, "• Replicas: 2 Instances\n• Course Registration Logic\n• Input Validation & Owner Checks\n• Static Course Catalog Fallback\n• Health: /healthz, /readyz", 
            ha='center', va='center', fontsize=8.5, color='#2D3748')

    # Service 2: Payment Service
    ax.add_patch(patches.FancyBboxPatch((5.1, 2.5), 3.8, 2.6, boxstyle="round,pad=0.2", fc="#FFFAF0", ec="#DD6B20", lw=1.8))
    ax.text(7.0, 4.8, "Payment Service (Port 8082)", ha='center', va='center', fontsize=11, fontweight='bold', color='#7B341E')
    ax.text(7.0, 3.7, "• Replicas: 2 Instances\n• Idempotency Keys\n• Atomic Transaction\n• Auto-Rollback on Interruption\n• Financial Audit Log", 
            ha='center', va='center', fontsize=8.5, color='#2D3748')

    # Service 3: Academic Records Service
    ax.add_patch(patches.FancyBboxPatch((9.7, 2.5), 3.8, 2.6, boxstyle="round,pad=0.2", fc="#FAF5FF", ec="#805AD5", lw=1.8))
    ax.text(11.6, 4.8, "Academic Service (Port 8083)", ha='center', va='center', fontsize=11, fontweight='bold', color='#44337A')
    ax.text(11.6, 3.7, "• Replicas: 2 Instances\n• Circuit Breaker (gobreaker)\n• Last-Known-Good Transcript Cache\n• Labelled Degraded Responses\n• DB Timeout (1.5s)", 
            ha='center', va='center', fontsize=8.5, color='#2D3748')

    # Connect Gateway to Services
    ax.annotate("", xy=(2.4, 5.2), xytext=(5.0, 6.0), arrowprops=dict(arrowstyle="->", lw=1.5, color="#38A169", ls="--"))
    ax.annotate("", xy=(7.0, 5.2), xytext=(6.3, 6.0), arrowprops=dict(arrowstyle="->", lw=1.5, color="#DD6B20", ls="--"))
    ax.annotate("", xy=(11.6, 5.2), xytext=(7.6, 6.0), arrowprops=dict(arrowstyle="->", lw=1.5, color="#805AD5", ls="--"))

    # Tier 4: Database Storage Tier
    ax.add_patch(patches.FancyBboxPatch((4.0, 0.3), 6.0, 1.6, boxstyle="round,pad=0.2", fc="#FFF5F5", ec="#E53E3E", lw=2))
    ax.text(7.0, 1.6, "PostgreSQL 15 (Single Node)", ha='center', va='center', fontsize=11, fontweight='bold', color='#9B2C2C')
    ax.text(7.0, 0.9, "• Known Single Point of Failure, no Standby in this Version\n• Tables: students, transcripts, courses, enrollments, payments, idempotency_keys\n• Pool: max 20 per Instance | Socket Deadline 1.5s", 
            ha='center', va='center', fontsize=8.5, color='#2D3748')

    # Connect Services to DB
    ax.annotate("", xy=(4.8, 1.9), xytext=(2.4, 2.5), arrowprops=dict(arrowstyle="->", lw=1.5, color="#4A5568"))
    ax.annotate("", xy=(7.0, 1.9), xytext=(7.0, 2.5), arrowprops=dict(arrowstyle="->", lw=1.5, color="#4A5568"))
    ax.annotate("", xy=(9.2, 1.9), xytext=(11.6, 2.5), arrowprops=dict(arrowstyle="->", lw=1.5, color="#4A5568"))

    # Tier 5: Observability Tier (Prometheus & Grafana)
    ax.add_patch(patches.FancyBboxPatch((10.0, 6.3), 3.5, 2.4, boxstyle="round,pad=0.2", fc="#FEFCBF", ec="#D69E2E", lw=1.8))
    ax.text(11.75, 8.4, "Observability Plane (Prometheus/Grafana)", ha='center', va='center', fontsize=10.5, fontweight='bold', color='#744210')
    ax.text(11.75, 7.3, "• Prometheus v2.48.0 (Port 9090)\n• Grafana Visual Dashboards (Port 3000)\n• 5 Alert Rules (instance down, error rate,\n  p95 latency, breaker open, rate limit)\n• Structured Logs + Correlation IDs", 
            ha='center', va='center', fontsize=8.0, color='#2D3748')

    # Scraping lines
    ax.annotate("", xy=(8.4, 7.3), xytext=(10.0, 7.3), arrowprops=dict(arrowstyle="<-", lw=1.2, color="#D69E2E", ls=":"))
    ax.annotate("", xy=(11.6, 5.2), xytext=(11.6, 6.3), arrowprops=dict(arrowstyle="<-", lw=1.2, color="#D69E2E", ls=":"))

    plt.tight_layout()
    plt.savefig("diagrams/architecture_diagram.png", dpi=300)
    plt.close()
    print("  [OK] Saved diagrams/architecture_diagram.png")

def mean_of(results, exp, target, key):
    values = [r[key] for r in results["experiments"][exp]["targets"][target]["runs"] if r.get(key) is not None]
    return sum(values) / len(values) if values else 0.0


def generate_comparison_charts():
    """Reads results/experiment_results.json, so the chart can never drift from the data."""
    with open("results/experiment_results.json", encoding="utf-8") as f:
        res = json.load(f)
    t = ["Midterm", "Endterm"]
    colors = ["#A0AEC0", "#2B6CB0"]
    panels = [
        ("E2: responses with real transcript data, DB frozen (%)", [mean_of(res, "E2", x, "real_data_during_fault_pct") for x in ("midterm", "endterm")]),
        ("E1: slowest request around the crash (ms)", [sum(r["failure_window"]["latency_max_ms"] for r in res["experiments"]["E1"]["targets"][x]["runs"]) / len(res["experiments"]["E1"]["targets"][x]["runs"]) for x in ("midterm", "endterm")]),
        ("E5: unauthorized attempts blocked (%)", [100 * mean_of(res, "E5", x, "denied") / mean_of(res, "E5", x, "total") for x in ("midterm", "endterm")]),
        ("E7: failed client attempts during crash", [mean_of(res, "E7", x, "failed_attempts") for x in ("midterm", "endterm")]),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), dpi=200)
    for ax, (title, values) in zip(axes.flat, panels):
        bars = ax.bar(t, values, color=colors, width=0.5)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.spines[["top", "right"]].set_visible(False)
        ax.bar_label(bars, fmt="%.1f", padding=3)
        ax.margins(y=0.15)
    fig.suptitle("Measured comparison: midterm baseline vs endterm (mean of repetitions)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig("diagrams/comparison_charts.png")
    plt.close()
    print("  [OK] Saved diagrams/comparison_charts.png")


def generate_threat_model_diagram():
    fig, ax = plt.subplots(figsize=(13, 7.5), dpi=300)
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 8)
    ax.axis('off')
    fig.patch.set_facecolor('#FFFFFF')

    ax.text(6.5, 7.5, "STRIDE Threat Model & Intrusion-Tolerance Defense Matrix", 
            ha='center', va='center', fontsize=15, fontweight='bold', color='#1A202C')
    ax.text(6.5, 7.1, "Resilient University Information System (AITU CSE-2501M)", 
            ha='center', va='center', fontsize=10, style='italic', color='#4A5568')

    headers = ["STRIDE Threat", "Target Vector in UIS", "Risk (L x I)", "Implemented Countermeasure", "Resilience & Safe State Outcome"]
    col_x = [0.5, 2.5, 5.2, 7.0, 10.0]
    widths = [1.8, 2.5, 1.6, 2.8, 2.6]

    # Header row
    for i, h in enumerate(headers):
        ax.add_patch(patches.Rectangle((col_x[i], 6.3), widths[i], 0.6, fc="#2B6CB0", ec="#2B6CB0"))
        ax.text(col_x[i] + widths[i]/2, 6.6, h, ha='center', va='center', fontsize=9.5, fontweight='bold', color='#FFFFFF')

    # Risk = Likelihood x Impact, same 1-5 scale and bands as Assignment 4.
    threats = [
        ("Spoofing Identity", "Forged or tampered JWT", "High (3x5=15)", "HS256 JWT, 2h expiry,\nno default secret", "401; audit event"),
        ("Tampering with Data", "Bad amount, SQL injection,\nreused idempotency key", "High (3x5=15)", "Input validation, parameterized SQL,\nkey checked against payment", "400/409/422; DB unchanged"),
        ("Repudiation", "User denies a payment", "Medium (2x4=8)", "AUDIT logs with correlation ID", "Traceable (logs are not tamper-proof)"),
        ("Information Disclosure", "Student reads another\nstudent's transcript", "Critical (4x5=20)", "RBAC and owner checks,\nbackends not published", "403; no TLS yet (limitation)"),
        ("Denial of Service", "Request bursts on the API", "High (4x4=16)", "Token bucket per IP (50 burst, 20/s),\n1 MB body limit", "429; service stays up"),
        ("Elevation of Privilege", "Client asks for Admin role", "Critical (4x5=20)", "Role taken from server table,\nRBAC at the gateway", "Role ignored; 403 logged"),
    ]

    y_pos = 5.4
    for threat, target, dread, counter, outcome in threats:
        row_color = "#F7FAFC" if (threats.index((threat, target, dread, counter, outcome)) % 2 == 0) else "#EDF2F7"
        for i, val in enumerate([threat, target, dread, counter, outcome]):
            ax.add_patch(patches.Rectangle((col_x[i], y_pos), widths[i], 0.8, fc=row_color, ec="#CBD5E0", lw=0.5))
            weight = 'bold' if i in [0, 2] else 'normal'
            txt_color = '#C53030' if i == 2 and 'Critical' in val else '#2D3748'
            ax.text(col_x[i] + 0.1, y_pos + 0.4, val, ha='left', va='center', fontsize=8.0, fontweight=weight, color=txt_color, wrap=True)
        y_pos -= 0.85

    plt.tight_layout()
    plt.savefig("diagrams/threat_model_stride.png", dpi=300)
    plt.close()
    print("  [OK] Saved diagrams/threat_model_stride.png")

if __name__ == '__main__':
    generate_architecture_diagram()
    generate_comparison_charts()
    generate_threat_model_diagram()
    print("All diagrams generated successfully.")
