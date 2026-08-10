# ArgoCD GitOps Catalog (public structure)

Declarative app-of-apps catalog for the Talos cluster, following the
**hks-argocd** three-tier Kustomize convention. This repo is split across two
repositories:

| PUBLIC (`Home-Ops`, this repo) | PRIVATE (the private config repo) |
|--------------------------------|-------------------------------|
| Tier 1 Application templates (`apps/<app>/app/`) | Tier 2 env overlays (`apps/<app>/environments/<env>/`) |
| README / docs | Tier 3 aggregator + projects (`environments/<env>/`) |
| pinned-version PLACEHOLDERs, wiring | all Helm values, all raw manifests, all pins |

Rule of thumb: **structure is public, config is private**. No environment data
-- chart version pins, hostnames, cluster/env names, Helm values, secrets --
ever lands in this public repo. Placeholders only.

## Ownership partition (permanent)

An object has exactly one reconciler.

- **OpenTofu** owns the substrate: the cluster, Cilium CNI, all `cilium.io` CRs
  (LB pools, L2/BGP, policies), and **ArgoCD itself** (a `helm_release` in a
  terragrunt unit). ArgoCD is never self-managed. Tofu also creates the root
  app-of-apps Application, which targets the private aggregator.
- **ArgoCD** owns only workloads above the platform (external-secrets,
  cert-manager, traefik today). This catalog defines those and nothing else.

The `infrastructure` AppProject (defined privately) enforces the boundary:
`argoproj.github.io/argo-helm` is banned from `sourceRepos`, and the `cilium.io`
group is blacklisted (cluster- and namespace-scoped).

## Deployment archetypes

Every app carries a `deploymentStrategy` label on its Tier 1 Application that
selects its directory shape and how the Tier 3 aggregator patches it.

| Strategy | Dirs | sources[] |
|----------|------|-----------|
| `helm-direct` | `apps/<app>/app/` + private `apps/<app>/environments/<env>/` | `[0]` external chart (version placeholder), `[1]` git `ref: values`; optional `[2]` git `path .../manifests` |
| `helm-umbrella` | `apps/<app>/app/` + private `apps/<app>/environments/chart/Chart.yaml` + `.../environments/<env>/` | `[0]` git path to local umbrella chart, `[1]` git `ref: values`, optional `[2]` git manifests |
| `kustomize` | `apps/<app>/app/` + private `apps/<app>/base/` + `apps/<app>/<env>/` | single `[0]` git `path apps/<app>/PLACEHOLDER` (no Tier 2) |

Today all catalog apps are `helm-direct`. The `helm-umbrella` and `kustomize`
stanzas exist in the aggregator for shape-fidelity and future use.

## Three tiers

- **Tier 1 -- base Application** (`apps/<app>/app/app.yaml`, PUBLIC): the
  environment-agnostic Application. Chart version is `targetRevision: "0.0.0"`,
  each private git source's `repoURL` is `PLACEHOLDER`, and the values / manifests
  paths carry an uppercase `PLACEHOLDER` segment. The public repo never names the
  private config repo. Wrapped by `app/kustomization.yaml` (`resources: [app.yaml]`).
- **Tier 2 -- environment overlay** (`apps/<app>/environments/<env>/`, PRIVATE):
  remote-bases the public Tier 1 (`resources: [github.com/NIXKnight/Home-Ops//argocd/apps/<app>/app?ref=main]`),
  then a JSON patch pins `sources[0].targetRevision`, injects the private config
  repo's URL into the git sources (`sources[1].repoURL`, plus `sources[2].repoURL`
  for manifests apps), rewrites the `$values` path to the concrete env values
  file, rewrites the `sources[2].path` manifests path (helm-direct-with-manifests
  apps), and adds `spec.info`. The env `values.yaml` and any `manifests/` sit
  beside it.
- **Tier 3 -- environment aggregator** (`environments/<env>/base/kustomization.yaml`,
  PRIVATE): lists the member apps, applies the universal cascade-delete
  finalizer, overrides the git branch per strategy, and (for kustomize apps)
  replaces the path PLACEHOLDER from an `env-config.yaml` ConfigMap. The entry
  point `environments/<env>/kustomization.yaml` resolves `./base`.

### Cross-repo base direction

The remote-base reference points **PRIVATE -> PUBLIC**: each private Tier 2
overlay pulls the public Tier 1 `app/` over `https` (public repo, no auth). The
repo-server needs egress to `github.com` for this, plus the private-repo SSH
credential (Tofu-managed, out of band) to fetch `$values` and the manifests
sources.

## Wave ladder

| Wave | Object |
|------|--------|
| `-1` | AppProject `infrastructure` |
| `0`  | `external-secrets` (ESO chart + ClusterSecretStore/CRB via chart `extraObjects`) |
| `1`  | `cert-manager` |
| `1`  | `zfs-localpv` (OpenEBS ZFS-LocalPV CSI + StorageClasses for the monitoring node) |
| `1`  | `cloudnative-pg` (CloudNativePG operator: installs the `postgresql.cnpg.io` CRDs + admission webhooks the `postgresql` app consumes) |
| `2`  | `traefik` (its Certificate needs cert-manager's CRDs + a ready ClusterIssuer) |
| `2`  | `external-dns-private` (publishes cluster Ingress hosts to the on-network DNS; needs ESO wave 0 for its API-key Secret) |
| `2`  | `postgresql` (cluster-wide shared PostgreSQL: single-instance CNPG `Cluster` on the storage worker over a dedicated ZFS StorageClass; serves `postgresql-rw`/`-ro`/`-r`) |
| `3`  | `prometheus` (metrics store: long-retention TSDB + remote-write receiver + platform alert rules; sole owner of the monitoring namespace metadata and the `observability-critical` PriorityClass the wave-4 apps consume) |
| `3`  | `bifrost` (OpenAI-compatible LLM gateway; config + logs in the shared PostgreSQL, provider/encryption keys via ESO, LAN Ingress) |
| `4`  | `loki` (log store on the monitoring node; consumes the wave-3 namespace + PriorityClass) |
| `4`  | `grafana` (dashboards + alerting UI over Prometheus and Loki) |
| `4`  | `alloy-metrics` (sole metrics collector: scrapes targets and remote-writes to Prometheus) |
| `4`  | `alloy-logs` (node log collector; rides the wave-3 privileged-PSA namespace label) |
| `4`  | `metrics-server` (serves the Metrics API for `kubectl top` / HPA from the control-plane nodes) |

Cross-Application wave gating -- one Application waiting for an earlier one to be
Healthy before the next wave starts -- relies on the ArgoCD Application health
customization configured in the ArgoCD install values (Tofu-owned), not in this
catalog.

Ordering inside an app's `manifests/` dir uses resource-level
`argocd.argoproj.io/sync-wave` annotations (e.g. ExternalSecrets `"1"` land
before the ClusterIssuers `"2"` that consume their Secrets; the wildcard
Certificate `"2"` before the TLSStore `"3"`).

## Adding an app (helm-direct)

1. **Tier 1 (here, public):** `cp -r apps/<existing>/app apps/<name>/app`, edit
   `app.yaml` -- name, chart repo/name, destination namespace, wave, and the
   `deploymentStrategy` label. Keep `targetRevision: "0.0.0"` and the uppercase
   `PLACEHOLDER` path segments. Do **not** pin a version or write any env data here.
2. **Tier 2 (private):** in the private config repo, create
   `argocd/apps/<name>/environments/<env>/kustomization.yaml`
   (remote-base the public `app/`, patch the pin + the private `repoURL`(s) +
   `$values` path + optional manifests path + `spec.info`) and `values.yaml`;
   add a `manifests/` dir for any raw CRs.
3. **Tier 3 membership (private):** in the private config repo, add
   `../../../apps/<name>/environments/<env>` to
   `argocd/environments/<env>/base/kustomization.yaml`.
4. **Project:** add the app's namespace (and any new chart repo / cluster
   resource) to the private `infrastructure` AppProject.

`apps/<app>/app/` is intentionally a bare template until a private Tier 2 makes
it a member of an environment.

## Render check (no apply)

```bash
# Tier 1 templates render (PLACEHOLDERs still present -- expected):
kubectl kustomize apps/traefik/app
# Full environment render is a PRIVATE-repo operation (needs the Tier 2 overlays).
```
