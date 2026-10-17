# Hindsight Kubernetes desired state

Hindsight 0.10.2 is registered in the private environment root. Its child Application
intentionally has **no automated sync**. This repository describes desired state only;
the direct inference route has not been live-accepted and must not be presented as
ready until a separately authorized manual Argo CD sync and runtime checks succeed.

## Public structure and private configuration

The public repository owns the three-source Argo CD Application shape and the offline
validator/tests. The private environment overlay pins the null-renderer chart and
supplies the environment manifests. The root common patch adds the cascade finalizer;
the Hindsight Application does not define one directly.

The AppProject permission remains narrow: the Application targets only the in-cluster
`hindsight` namespace, repositories are explicitly allowlisted, cluster-scoped Cilium
resources remain denied, and only namespaced `CiliumNetworkPolicy` is permitted from the
Cilium API group.

## Approved image compatibility exception

The Operator approved upstream Hindsight 0.10.2 immutable images even though the image
source revision differs from the manifest compatibility-review revision. Both images
remain digest-pinned. The reviewed images run as UID 1000, contain the local
embedding/reranker caches, and support the authored probes and environment variables.
`readOnlyRootFilesystem: false` remains deliberate. API model initialization is bounded
to 540 seconds inside the 600-second startup budget.

## Shared database ownership

The existing PostgreSQL Application owns the Hindsight database, least-privilege owner
role, and owner credential ExternalSecret in namespace `postgresql`. Database and role
reclaim policies remain `retain`, with Argo prune/delete guards. The Hindsight
Application owns no PostgreSQL data-plane resources.

The API connects to `postgresql-rw.postgresql.svc.cluster.local:5432`, database and role
`hindsight`, schema `public`, with pool minimum/maximum 1/10 and role connection limit
20. The explicit Operator decision remains `PGSSLMODE=disable`. The DSN and application
authentication values remain individual, required SecretKeyRefs and must never be
printed or inspected.

## Direct llama.cpp inference contract

Hindsight bypasses Bifrost and uses the LAN llama.cpp OpenAI-compatible endpoint
directly. The four exact, intentionally public literals are:

- `HINDSIGHT_API_LLM_PROVIDER=openai`
- `HINDSIGHT_API_LLM_MODEL=qwen3.8-27b`
- `HINDSIGHT_API_LLM_BASE_URL=https://llamacpp.h.nixknight.pk/v1`
- `HINDSIGHT_API_LLM_API_KEY=not-required`

The `/v1` suffix is mandatory. Provider `llamacpp` is not valid for this route in
Hindsight 0.10.2 because it selects Hindsight's built-in local implementation rather
than a remote OpenAI-compatible server. The model is the direct server alias, not the
former Bifrost-qualified model.

Hindsight 0.10.2 requires a nonempty API key when provider `openai` is selected. The
literal `not-required` is a non-secret compatibility sentinel, not a credential and not
a Kubernetes Secret. Hindsight may send `Authorization: Bearer not-required`; llama.cpp
remains intentionally unauthenticated because the LAN server does not enforce that
header. This does **not** disable Hindsight's own tenant API-key authentication.

The inference ExternalSecret and every inference SecretKeyRef are absent, so the former
Bifrost credential has no path into requests to llama.cpp. The Hindsight workload now
owns exactly two ExternalSecrets: application authentication and the database
connection.

## Network and LAN boundary

Namespace default-deny remains enabled for ingress and egress. The only inference
exception selects the Hindsight API and permits:

- `toFQDNs.matchName: llamacpp.h.nixknight.pk`
- TCP 443 only.

It does not permit `world`, the LAN CIDR, any broad CIDR, wildcard FQDNs, TCP 80, or the
former Bifrost TCP 8080 path. The existing kube-dns TCP/UDP 53 policy includes only the
Cilium DNS L7 observation rule required to populate FQDN policy state. Other database,
UI/API, and Traefik flows remain unchanged.

The llama.cpp endpoint is exposed only within the trusted LAN boundary by surrounding
DNS/routing controls; it has no application-level API key enforcement. This change does
not alter llama.cpp, nginx, Traefik, external-dns, DNS, or Bifrost configuration.

The Hindsight manifest inventory is exactly 19 resources: namespace, two Deployments,
two ClusterIP Services, two ServiceAccounts, two workload ExternalSecrets, two
Ingresses, and eight namespaced Cilium policies.

## Availability limitation

Direct routing removes Bifrost from the request path but does not create GPU capacity.
A `502` from the LAN endpoint can still occur when llama.cpp does not own the GPU, is
stopped, or cannot load/serve the configured model. Repository validation cannot prove
GPU ownership, endpoint availability, TLS/DNS behavior, or inference success.

## Acceptance state and activation

The shared-database acceptance evidence is preserved. Inference-route approval and
Operator preflight/runtime acceptance are reset to false because the new path has not
been exercised live. Scaffolding validation should pass. Normal offline validation is
expected to report only:

- `INFERENCE_EGRESS_UNAPPROVED`
- `OPERATOR_PREFLIGHT_AND_RUNTIME_ACCEPTANCE_UNAPPROVED`

After publication, a separately authorized operator must manually sync and verify DNS
resolution, Cilium FQDN enforcement, TLS connectivity, API/UI health, tenant
authentication, and a synthetic completion against the exact model. Only a reviewed
follow-up may record those acceptance gates as true. Do not combine publication with a
live sync or claim readiness from offline checks.

## Credential rotation

No automatic reloader is configured. Database/authentication secret changes require a
controlled restart of affected consumers and synthetic reconnect/authentication checks.
Inference settings are literals and no longer participate in secret rotation.

## Rollback

Rollback is a reviewed Git revert followed by a separately authorized reconciliation.
A rollback to Bifrost must coherently restore the prior inference ExternalSecret,
required SecretKeyRefs, Bifrost-qualified model and `/v1` base URL, API-to-Bifrost
TCP 8080 policy, contract/tests/docs, and then repeat live acceptance. Never reuse or
copy the former credential into the direct llama.cpp configuration. Retained database
resources are not pruned by this route change.

## Validation boundary

Validation is offline and secret-safe: parse only approved files, validate local
Kustomizations and contracts, run focused tests and Python formatting checks, and inspect
diffs. Do not contact a cluster or endpoint, invoke Kubernetes/Helm/Argo tools, inspect
secret values or kubeconfigs, mutate secret stores, stage, commit, push, or sync. Redact
accidental sensitive material as `<REDACTED>`.
