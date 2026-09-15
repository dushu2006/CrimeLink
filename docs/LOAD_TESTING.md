# Load Testing — Priority 3.1

## Goals

- Prove pipeline can handle 100 docs/hour sustained (PRD 15: 8-container prod)
- Prove AI gateway latency <30s p95 after Phase 1-3 optimizations
- Prove ER queue SLA 48h not breached under load
- Prove rate limiting does not false-positive under normal load, but blocks credential stuffing

## Tools

- **k6** (preferred): JS scenarios, Prometheus output, Grafana visualization
- **Locust**: Python, easier for complex workflows, but k6 is enough

## Scenarios

### 1. Document Ingestion Throughput

```javascript
// k6 script: ingest_docs.js
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  stages: [
    { duration: '5m', target: 10 },  // ramp to 10 VUs
    { duration: '10m', target: 10 }, // sustain 10 concurrent uploads
    { duration: '2m', target: 0 },
  ],
  thresholds: {
    'http_req_duration': ['p(95)<2000'],
    'http_req_failed': ['rate<0.05'],
  },
};

export default function () {
  const data = {
    file: http.file(open('./sample.pdf', 'b'), 'sample.pdf'),
    case_id: 'LOAD_TEST_CASE',
  };
  const res = http.post('http://localhost:80/api/v1/documents/upload', data, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
  });
  check(res, { 'upload ok': (r) => r.status === 202 });
}
```

Run:
```bash
k6 run --out prometheus=namespace=k6 ingest_docs.js
# Watch Grafana: crimelink_documents_processed_total rate, er_queue_pending
```

Expected: `crimelink_documents_processed_total` rate >= 100/hour, `quarantine_documents` <5%

### 2. AI Gateway Latency (Phase 1-3 Validation)

```javascript
// k6 script: ai_ask.js
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 5,
  duration: '10m',
  thresholds: {
    'http_req_duration': ['p(95)<30000'], // 30s p95 after optimizations
  },
};

export default function () {
  const res = http.post(`http://localhost:80/api/v1/ai/cases/${__ENV.CASE_ID}/ask`,
    JSON.stringify({ question: 'Who are the key persons in this case?' }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${__ENV.TOKEN}` } }
  );
  check(res, { 'ai ok': (r) => r.status === 200, 'has evidence': (r) => r.json().evidence_refs.length > 0 });
}
```

Before Phase 1-3: p95 ~45-60s, timeouts 10-15% (DeepSeek)
After Phase 1-3: p95 ~12-18s, timeouts <1% (measured in docs/AI_LATENCY_INVESTIGATION.md)

### 3. Auth Rate Limiting (Credential Stuffing Defense)

```javascript
// k6 script: auth_flood.js
import http from 'k6/http';

export const options = {
  vus: 20,
  duration: '1m',
};

export default function () {
  const res = http.post('http://localhost:80/api/v1/auth/login',
    JSON.stringify({ username: 'attacker', password: 'wrong' }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  // Expect 429 after 10 per minute per IP
}
```

Verify: `rate(crimelink_api_requests_total{status="429"}[1m])` spikes, but legitimate user from different IP not blocked (Redis shared counter proves horizontal scaling).

### 4. Graph Expansion (Heavy Query)

```javascript
// k6 script: graph_expand.js
import http from 'k6/http';

export const options = { vus: 10, duration: '5m' };

export default function () {
  http.get(`http://localhost:80/api/v1/graph/person/${__ENV.PERSON_ID}?depth=2`, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
  });
}
```

Threshold: p95 <500ms for depth=2, node limit 300 (config `CRIMELINK_GRAPH_MAX_EXPAND_DEPTH`)

## Running

```bash
# Install k6: https://k6.io/docs/getting-started/installation/
# Get token:
TOKEN=$(curl -s http://localhost:80/api/v1/auth/login -d '{"username":"admin","password":"admin"}' -H "Content-Type: application/json" | jq -r .access_token)

# Run all scenarios:
k6 run -e TOKEN=$TOKEN -e CASE_ID=<uuid> ai_ask.js
k6 run -e TOKEN=$TOKEN ingest_docs.js
```

## Prometheus Integration

k6 can output to Prometheus remote write, then Grafana shows k6 metrics alongside crimelink metrics.

Add to `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'k6'
    static_configs:
      - targets: ['k6:6565']
```

## Success Criteria (Priority 3)

- [ ] 100 docs/hour sustained for 1h, no worker OOM, ER queue <100
- [ ] AI ask p95 <30s, p99 <45s, timeout rate <1%
- [ ] Auth endpoint 429 after 10/min/IP, general API 100/min/user
- [ ] Graph expand depth=2 p95 <500ms, depth=3 still <2s
- [ ] No 5xx >1% during load

## Current State

- No k6 scripts yet — this doc is the spec
- Phase 1-3 latency numbers recorded in `docs/AI_LATENCY_INVESTIGATION.md`
- CI does not run load tests (too heavy) — run manually before prod
