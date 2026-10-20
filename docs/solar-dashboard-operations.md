# Hybrid Solar Plant dashboard: source, validation and rollout

This is a **source-only repair**, not evidence that either live Grafana has changed.
The collector, Telegraf inputs and both dashboards must be rolled out together by
an authorized Operator. Do not manipulate Grafana's database or extract tokens.

## One source, two explicit profiles

Canonical JSON: `ansible/files/monitoring/hybrid-solar-plant.json`.
Renderer: `utils/render-solar-dashboard.py` (Python standard library).

The cluster manifest in the companion repository is generated from this source:

```sh
python -B utils/render-solar-dashboard.py --profile cluster --check-manifest \
  ../Home-Ops-Internal/argocd/apps/grafana/environments/belisarius/manifests/grafanadashboard-hybrid-solar.yaml
# After intentionally editing canonical source, regenerate the LOCAL manifest:
python -B utils/render-solar-dashboard.py --profile cluster --write-manifest \
  ../Home-Ops-Internal/argocd/apps/grafana/environments/belisarius/manifests/grafanadashboard-hybrid-solar.yaml
```

Cluster profile: dashboard UID `hybrid-solar-plant`, Prometheus UID `prometheus`,
nominal ingestion cadence 5s. Existing GrafanaDashboard/plugin provisioning remains.

**Do not import the canonical/cluster JSON into the gateway.** Its live dashboard
UID and datasource mapping have not been independently confirmed (public GET was
401). No gateway provider, mount, guessed identity or automatic upload is added.

The Operator must confirm BOTH existing UIDs in the authenticated gateway UI:
Dashboard settings/JSON model and its selected Prometheus datasource. Then render
a local import file explicitly, using these non-secret IDs (not passwords/tokens):

```sh
python -B utils/render-solar-dashboard.py --profile gateway \
  --dashboard-uid "${EXISTING_DASHBOARD_UID:?confirm existing gateway dashboard UID}" \
  --datasource-uid "${EXISTING_DATASOURCE_UID:?confirm existing gateway datasource UID}" \
  > hybrid-solar.gateway.json
```

The renderer refuses missing/invalid gateway identities. It never contacts Grafana.
Import the resulting file through the Operator's authenticated gateway UI, confirm
that its UID resolves to the intended existing Hybrid Solar Plant dashboard,
review datasource selection, and explicitly choose overwrite. Abort on an identity
mismatch; do not create a duplicate. Gateway profile uses the same panel/query/code
content, with nominal cadence 10s. Tests constrain differences to identities/cadence.
Keep the previous dashboard version/export available to the Operator for rollback.

## Producer contract and bounded snapshots

Both solar exec definitions opt in to `--emit-poll-status`. Existing metric floats,
measurement `deye_inverter`, host tag, 5s/300s polling, 4s/30s timeouts, 5s flush,
60s exporter cache expiry and unrelated outputs are unchanged.

- `telemetry_poll_timestamp_seconds`: producer wallclock **before** read attempts;
  `telemetry_poll_fields_received/expected`; `telemetry_poll_success` (all 66 read).
- `fault_state_known`: 1 when an active fault is observed OR all six words are zero;
  otherwise 0. `fault_active` is omitted on unknown reads, not fabricated as zero.
- `grid_total_power_read_success`: successful decode, **including a true 0 W**.
- Settings has separate `settings_poll_timestamp_seconds`, fields received/expected
  and poll success (all 16 read), at a nominal 300s cadence.

Fast values need producer age **-5 <= age < 30s** and complete telemetry. The 5s
negative tolerance accommodates small clock skew, not arbitrary future clocks.
Fault views instead use fresh + fault-known, preserving positive partial-read
faults. Grid power uses fresh + grid-read-success, never a power >0 predicate.
Other fast values are conservatively unavailable on incomplete polls. A killed
exec cannot emit diagnostics, so timestamp age independently expires old values.

Settings uses `last_over_time(...[10m])` on values AND latest diagnostic state,
requiring successful latest settings poll and producer age **<600s**. This bridges
both the local exporter's 60s expiry and observed 300–307s polling gaps without
increasing global cache expiry or hiding stale fast telemetry. Age is the numeric
producer timestamp, never `timestamp(exported_gauge)`. The health panel retains
old heartbeat observations to display age; that does not make physical data valid.

## Meaning and limitations

- Grid 0 W is valid when protectors open or utility supply fails. Connection status
  is **grid available/unavailable**, not proof of utility-only failure. No export
  functionality or blanket zero rejection is introduced.
- Missing/nonfinite/ambiguous Power Flow values say Unavailable; any unknown node
  suppresses arrows. Current nulls are not replaced by earlier non-null values.
- Qualified histories require diagnostics at their evaluation time. Earlier-day
  comparisons shift poll health too. Pre-diagnostic observations are not assigned
  fabricated health, zero thermal exposure, connection percentages or counts.
- Connection percentage uses qualified observations. Disconnection count uses
  per-series `floor((changes + 1 - last_state)/2)` on a 5s/10s qualified observation
  grid. Initially disconnected is not an observed onset. Gaps/resampling can conceal
  events; this is not a continuous-duration availability SLO.
- Heartbeat sample coverage is received diagnostic samples / nominal expected
  samples (clamped to 100%); complete-poll percentage is among received diagnostics.
  Neither reconstructs missed device reads, elapsed outages or unknown intervals.
- Hot/out-of-band minute observations are boolean sums on a one-minute grid, not
  exact elapsed durations. Observed below-band values yield labeled zero; absent
  qualified observations remain unavailable. Battery 45°C orange is an observation
  band, not a manufacturer safety/charge threshold. BMS limit/temperature register
  semantics still need authoritative pack/firmware confirmation.
- Discharge/charge energy over 3d is a throughput ratio, not physical round-trip
  efficiency. Charge+discharge kWh is throughput, not cycles. Grid/load and positive
  input/load ratios do not allocate charging, losses or auxiliary-port boundaries.
  There is no UPS=load-GEN inference, reserve-aware backup-runtime claim, assumed
  LFP floor or PV string-failure diagnosis from mismatch alone.
- Trailing 24h energy is a line of overlapping lifetime-register increases, NOT
  local calendar-day buckets; do not sum overlapping points. `increase` extrapolates
  edges. Any counter decrease invalidates its entire window instead of counting a
  zero/restoration glitch as production. Real resets are also unavailable; no
  unsupported correction is invented. Device day/month/year use inverter clocks.
- Null bridging is disabled. Prometheus lookback may still hold the last qualified
  observation within the bounded freshness budget; graphs are sampled, not continuous.

## Validation and rollout boundary

```sh
python -B ansible/tests/test_hybrid_solar_dashboard.py -v
# Explicit opt-in: existing kubectl auth, GET-only Kubernetes service proxy;
# no kubeconfig/credential reading, listeners, port forwards or remote mutations.
python -B ansible/tests/test_hybrid_solar_dashboard.py --prometheus -v
```

The GET route is the existing Kubernetes API service proxy (no port-forward):
`/api/v1/namespaces/monitoring/services/http:prometheus-server:80/proxy/api/v1/query?query=<URL-encoded-PromQL>`.
The test helper invokes `kubectl --request-timeout=40s get --raw <route>` and never
prints authentication-plugin diagnostics. To independently recheck a selected
canonical expression, without exporting/importing a dashboard:

```sh
python -B - <<'PY'
import runpy
checks = runpy.run_path('ansible/tests/test_hybrid_solar_dashboard.py', run_name='solar_check')
dashboard = checks['renderer'].render('cluster')
# Panel 41/H: observed disconnections; change panel/ref only as needed.
query = checks['substitute'](checks['target'](dashboard, 41, 'H')['expr'], 5)
rows = checks['prometheus'](query)
print({'series': len(rows), 'numeric_values': [float(row['value'][1]) for row in rows]})
PY
```

Tests check canonical/profile parity, query gates and actual collector pure-function
contracts, all substituted profile queries, real ECharts execution, labeled thermal
zero vs absence, counter-reset exclusion, settings expiry/jitter/failure, and 2046
binary disconnection sequences. Independent engine checks evaluated 124 unique
cluster/profile expressions on 3.13.1 and 110 gateway-profile expressions through
read-only host-local GETs on 2.53.1. These do not establish browser rendering
(Grafana 10.2.8 versus cluster 13.0.1) or deployed fixes.

The engines report a counter-name heuristic for five existing lifetime series:
`total_production`, `total_load_consumption`, `total_energy_bought`,
`total_battery_charge`, `total_battery_discharge` (all `deye_inverter_` prefixed).
Their names do not END in `_total/_sum/_count/_bucket`; the exporter declares them
untyped. 3.13.1 returns these five as informational annotations; 2.53.1 places them
in its warnings envelope despite the `PromQL info` text. Producer register definitions
establish cumulative kWh semantics; queries retain legacy names, guard decreases,
and do not rename/type-change the existing metric contract merely to hide a naming
heuristic. No query parse/evaluation failures were observed.

Future Operator rollout order: collector plus both exec flags; verify new health
fields and failed/partial behavior; then reconcile the cluster CR and import the
explicit-identity gateway profile. Before diagnostics exist, unavailable panels
are intentional. Confirm both datasources, both ingestion cadences and both UIs.
No rollout, commit, restart, source-state fabrication or remote write is performed
by the renderer/tests. Counter-discontinuity cause and physical energy-allocation
boundaries remain unresolved.
