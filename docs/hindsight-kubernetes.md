# Hindsight Kubernetes activation configuration

Hindsight is registered in the private environment root and is currently live
`Synced`/`Healthy`. Its child Application intentionally has **no automated sync**;
future reconciliations remain separate, explicitly authorized manual Operator actions.

## Public structure and private configuration

The public repository owns the three-source Argo CD Application shape and the offline
validator/tests. The private environment overlay pins the null-renderer chart, supplies
the environment manifests, and is now a root member. The root's common patch adds the
cascade finalizer; the Hindsight Application does not define a finalizer directly.

The AppProject permission remains narrow: the Application targets only the in-cluster
`hindsight` namespace, repositories are explicitly allowlisted, cluster-scoped Cilium
resources remain denied, and only namespaced `CiliumNetworkPolicy` is permitted from the
Cilium API group.

## Approved image compatibility exception

The Operator approved upstream Hindsight 0.10.2 immutable images even though the image
source revision differs from the revision against which these manifests were reviewed:

- image source revision: `5fc4ce20917b916240cef27c212c387a177f115b`;
- manifest compatibility-review revision: `f7dd3f4fd7420f7beec60c32c965e5e5cf7be066`;
- version: `0.10.2`;
- source mismatch and image provenance: explicitly approved.

The API and UI Deployments record these facts as separate annotations rather than a
misleading single source revision. Both images are digest-pinned. The reviewed images
run as UID 1000, contain complete local embedding/reranker model caches, and support the
authored probes and environment variables. Offline/local-model controls remain enabled.
`readOnlyRootFilesystem: false` is deliberate. The API uses `HOME=/home/hindsight`, the
UI uses `HOME=/home/node`, and API model initialization is bounded to 540 seconds inside
the existing 600-second startup budget.

## Shared database ownership

The existing PostgreSQL Application owns the Hindsight database resources in the
`postgresql` namespace:

- logical database and least-privilege owner role named `hindsight`;
- owner credential ExternalSecret used by CloudNativePG;
- `vector`, `pg_trgm`, and `btree_gin` in schema `public`.

Database and role reclaim policies remain `retain`, with Argo prune/delete guards. The
Hindsight Application owns no PostgreSQL data-plane resources.

The API connects to `postgresql-rw.postgresql.svc.cluster.local:5432`, database
`hindsight`, role `hindsight`, schema `public`. Pool minimum/maximum are 1/10 and the
role connection limit is 20. The explicit Operator decision remains
`PGSSLMODE=disable`; do not add PostgreSQL TLS resources or projections.

The DSN and credentials remain nonoptional, individual SecretKeyRefs. Never place,
render, inspect, or print their values. The shared-database approval is true only after
separately authorized live acceptance verified readiness.

## Inference contract

Inference uses the existing in-cluster Bifrost server through its OpenAI-compatible
`/v1` API. The expected nonsecret destination is:

`http://bifrost.bifrost.svc.cluster.local:8080/v1`

Provider, model, base URL, and API key remain existing individual SecretKeyRefs. No
secret value is authored or inspected. Their nonsecret expected contract is exact:
provider `openai`, model `llamacpp/qwen3.8-27b`, and the Bifrost `/v1` destination above.
The managed API-key value remains the existing Bifrost virtual key named `hindsight`,
which is restricted to provider `llamacpp`; the credential itself must never be read or
rendered.

The inference approval is recorded as true from the Operator's explicit decision. The
only inference egress policy selects the Hindsight API and the live Bifrost server pod
identity using namespace `bifrost` plus all three labels
`app.kubernetes.io/component=server`, `app.kubernetes.io/instance=bifrost`, and
`app.kubernetes.io/name=bifrost`, limited to TCP 8080. There is no world, CIDR, FQDN,
HuggingFace, or other Internet egress.

## LAN UI/API and network policy

The UI and API have separate standard `networking.k8s.io/v1` Ingress resources with
`ingressClassName: traefik`. The API route is exactly
`hindsight-api.h.nixknight.pk` and targets the `hindsight-api` ClusterIP Service on
TCP 8888. Both hosts-only TLS entries omit `secretName`, so Traefik uses the
environment's default TLSStore wildcard certificate, and external-dns-private discovers
both hosts from their Ingress rules. Both Services remain `ClusterIP`; no
`LoadBalancer`, `NodePort`, or external IP is introduced.

No middleware annotation is added to the API Ingress. The UI retains its existing access
key flow, while the API continues to require Hindsight's tenant API key extension; LAN
exposure does not bypass or replace application authentication. The Traefik ingress
policies select only the observed Traefik pod labels in namespace `traefik`, permitting
TCP 3000 to the UI and TCP 8888 to the API. The existing direct UI-to-API flow remains
narrowly allowed. All other ingress remains default-denied.

The Hindsight manifest inventory is exactly 20 resources, including two Ingresses and
eight namespaced `cilium.io/v2` policies. Those policies provide exactly:

1. namespace-wide ingress/egress default deny;
2. DNS to kube-dns on TCP/UDP 53;
3. UI egress to API on TCP 8888;
4. matching API ingress from UI on TCP 8888;
5. API egress to the shared PostgreSQL pods on TCP 5432;
6. API inference egress to Bifrost on TCP 8080;
7. UI ingress from Traefik on TCP 3000; and
8. API ingress from Traefik on TCP 8888.

## Credential rotation

Secret-backed environment variables are read only when a process starts. No automatic
reloader is configured. After an approved credential or inference-setting rotation,
perform a controlled restart of each affected consumer and verify reconnect/auth with
synthetic, non-sensitive data.

## Two-phase publication and manual activation

1. **Public phase:** publish the public Application/validator/docs changes first, then
   independently verify the public commit and availability of the remote base.
2. **Private phase:** only after that verification, publish the private overlay,
   manifests, contract, and root membership. Independently verify the root render and
   resulting child Application. Root reconciliation may create the child, but the child
   must remain OutOfSync/manual until the live gates below are cleared.
3. Verify shared database resources are ready without reading credential values; then
   record the shared-database approval in a separately reviewed change.
4. Complete Operator preflight and runtime acceptance, record that approval separately,
   and only then manually sync the Hindsight child Application.

Do not combine publication with a live Argo sync. Rollback is a reviewed Git revert
followed by an explicitly authorized reconciliation; retained database resources are not
pruned.

## Activation acceptance record

Offline activation validation must exit successfully with no blockers. All activation
approvals are true after separately authorized live acceptance recorded: Argo Hindsight
Synced/Healthy; all three ExternalSecrets Ready; both Deployments 1/1; API `/health`
HTTP 200 healthy; UI `/api/health` HTTP 200 with the dataplane connected; shared
database owner and extensions present with 25 public tables; and Bifrost completion
HTTP 200 through the approved virtual key/model path. File validation does not
independently prove those live facts.

## Validation boundary

Parse all YAML, render local Kustomizations, validate the Cilium v2 and Ingress schemas,
verify AppProject/root output, run the focused validator/tests, and format-check Python.
Read-only cluster schema and nonsecret workload-label checks are allowed. Do not mutate a
cluster or Argo CD, inspect secret values, alter secret stores, commit, push, or sync.
Preserve unrelated work and redact accidental sensitive material as `<REDACTED>`.
