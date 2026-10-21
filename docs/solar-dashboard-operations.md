# Hybrid Solar Plant dashboard: source, validation and rollout

This is a **source-only dashboard repair**, not evidence that either live Grafana
has changed. Poll diagnostics have been deployed since 2026-10-20; dashboard source
and generated cluster manifest still require an authorized Operator rollout. Do not
manipulate Grafana's database or extract tokens.

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

Poll diagnostics begin only in the newer retained data. There is deliberately no
hard-coded deployment timestamp in the dashboard: historical graphs query raw
measurements, while current snapshots fail closed and range aggregates report
coverage separately. Missing diagnostics before their deployment remain absent,
not fabricated as healthy or as zero.

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
- Current status/value panels require fresh producer timestamps and the applicable
  complete-poll, grid-read or fault-known predicate. Settings snapshots retain the
  separate ten-minute value/diagnostic lookback, successful latest poll and producer
  age under 600 seconds.
- Historical range graphs deliberately use raw retained measurements. This restores
  pre-diagnostic history removed by internal commit `8cb7511`; `714f7a5` is the last
  pre-regression dashboard baseline. Null bridging remains disabled, so missing
  retained samples appear as gaps. Older observations are legacy/unqualified and
  must not be described as freshness-validated current readings.
- Historical fault timeline values are also raw legacy observations. The current
  fault stat alone requires `fault_state_known` plus freshness.
- Selected/fixed-window aggregates operate on raw measurements and expose complete
  expected-sample coverage separately. Coverage is absent, not zero, before poll
  diagnostics existed. A low or missing coverage value limits confidence without
  deleting the aggregate.
- Connection percentage uses raw observed states. Disconnection count retains
  per-series `floor((changes + 1 - last_state)/2)`. Initially disconnected is not an
  observed onset. Gaps/resampling can conceal events; this is not a continuous-duration
  availability SLO. Heartbeat coverage and complete expected-sample coverage are
  shown separately.
- Hot/out-of-band minute observations are boolean sums on a one-minute grid, not
  exact elapsed durations. Observed below-band values yield labeled zero; absent
  observations remain unavailable. Battery 45°C orange is an observation
  band, not a manufacturer safety/charge threshold. BMS limit/temperature register
  semantics still need authoritative pack/firmware confirmation.
- Discharge/charge energy over 3d is a throughput ratio, not physical round-trip
  efficiency. Charge+discharge kWh is throughput, not cycles. Grid/load and positive
  input/load ratios do not allocate charging, losses or auxiliary-port boundaries.
  There is no UPS=load-GEN inference, reserve-aware backup-runtime claim, assumed
  LFP floor or PV string-failure diagnosis from mismatch alone.
- **Device-Day Energy — Gross Flows (not load attribution)** requests 30 days and
  displays five direct inverter-maintained counters as one non-stacked bar series
  per completed device day: load consumed, PV produced, grid imported, battery
  discharged and battery charged. Load is directly measured; the other series are
  gross flows, not allocation of load. Do not sum them. The cross-version time-series
  panel uses transparent bar overlays because it cannot group separate Prometheus
  frames side by side. Panel 17 intentionally remains the current partial device day.
- Read-only 30-day checks observed 28 load-counter resets in the cluster store; the
  shorter gateway retention previously exposed 16. Five recent resets appeared at
  `2026-10-16T19:01:40Z`–`19:01:50Z`, `2026-10-17T19:01:40Z`–`19:01:50Z`,
  `2026-10-18T19:01:35Z`–`19:01:50Z`, `2026-10-19T19:01:35Z`–`19:01:50Z`, and
  `2026-10-20T19:01:35Z`–`19:01:40Z` across the two stores. This is device midnight
  (19:00 UTC / 00:00 Asia/Karachi) plus collection latency.
- Each panel-52 energy query is evaluated once per UTC day and applies
  `max_over_time(metric[23h59m] offset 4h59m)` to raw samples from 19:02 UTC through
  19:01 UTC. The observation-safe interior excludes both adjacent delayed-reset
  samples. A marker at `00:00Z` represents the immediately preceding Asia/Karachi
  device day. Missing windows remain absent, all-zero days remain zero, and an
  isolated false zero cannot lower a day’s maximum; a false high sample can still
  inflate it. A separate right-axis line reports complete expected-sample coverage
  for the diagnostics era and is absent before diagnostics existed. Grafana aligns
  the 1d Prometheus step to UTC; an explicitly selected absolute range overrides the
  panel's 30-day relative request.
- Null bridging is disabled. Prometheus native lookback/staleness still applies;
  graphs are sampled, not continuous.

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
print({'series': len(rows)})  # Never print household measurements.
PY
```

Tests check canonical/profile parity, the explicit current/historical/aggregate/
settings/diagnostic policy matrix, raw pre-diagnostic history, coverage absence,
daily-step panel-52 semantics, current stale/partial suppression, valid zero, actual
collector pure-function contracts, all substituted profile queries, real ECharts
execution, settings expiry/jitter/failure, and 2046 binary disconnection sequences.
A 30-day, 1d-step range evaluation returned UTC-aligned date markers for each of the
five raw panel-52 energy bars. Read-only checks evaluate both rendered profiles on
cluster Prometheus; a separate safe host-local GET audit may evaluate all unique
queries on gateway Prometheus 2.53.1 without reading credentials or printing values.
Grafana 10.2.8 supports the encoded time-series defaults (bars) and per-series `F`
overrides (`custom.drawStyle=line`, zero fill, right axis, percent unit). Source-only
checks do not establish deployment or browser-renderer parity.

For this source revision, each rendered profile has 132 unique PromQL expressions;
the union is 150 because cadence-dependent expressions differ. The union evaluated
successfully through the read-only cluster Prometheus 3.13.1 service proxy with no
warnings (two informational counter-name annotations). A native gateway 2.53.1
recheck still requires a working authorized host-local SSH/GET route; do not replace
that check with credentials, secret-file inspection, a listener or a browser session.

The engines report a counter-name heuristic for the two lifetime series still used
with `increase`: `total_battery_charge` and `total_battery_discharge` (both
`deye_inverter_` prefixed). Their names do not end in
`_total/_sum/_count/_bucket`; the exporter declares them untyped. 3.13.1 returns
two informational annotations; 2.53.1 places two annotations in its warnings
envelope despite the `PromQL info` text. Producer register definitions establish
cumulative kWh semantics; queries retain legacy names, guard decreases, and do not
rename/type-change the existing metric contract merely to hide a naming heuristic.
No query parse/evaluation failures were observed.

## Focused producer-field coverage audit

The dashboard has 39 data panels and 157 targets: 65 current snapshots, 35 raw
historical targets, 23 raw aggregate measurements, 15 settings snapshots, 13 core
diagnostics and 6 aggregate coverage targets. It directly references 63 of the
82 register fields plus the required derived/poll-health fields. The 19 unreferenced
register fields were reviewed rather than mechanically graphed:

- `daily_energy_sold`, `total_energy_sold`, and `total_grid_production` stay omitted:
  this installation has no grid export and no export feature is implied.
- Six raw `fault_*` words stay omitted: `fault_active`, BMS alarm/fault and the status
  timeline already expose trustworthy state, while bit-to-fault decoding lacks an
  authoritative map for this exact model/firmware.
- Three external-CT values stay omitted because the CT is not wired/configured.
- Generator-ready stays omitted; the auxiliary port’s installed role is unverified.
- Alternate battery/BMS SoC, voltage, temperature and maximum-limit registers stay
  omitted because existing selected measurements and limits cover operations, and
  their comparative semantics are not authoritative enough for a new discrepancy
  panel.

No extra panel was added. Data-quality/poll health, battery reserve/configuration,
grid/protector unavailable observations, and current-day summary are already
covered. A daily power/energy-balance residual was rejected because conversion
losses and auxiliary boundaries would make it an unattributable residual rather
than a reliable accounting signal. Panel 39’s proxy/ratio names and description
remain explicit; its mixed arithmetic/state contents do not justify another panel.

For an authorized dashboard rollout, reconcile the cluster CR and import the
explicit-identity gateway profile. Confirm both datasources, both ingestion cadences
and both UIs. No rollout, commit, restart, source-state fabrication or remote write
is performed by the renderer/tests. Physical energy-allocation boundaries remain
unresolved.
