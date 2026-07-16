# Deployment (live, on AWS)

Deployed live on AWS, **right-sized on purpose**. The goal is a clean, defensible cloud
setup — not a service zoo. Everything is containerized; each piece maps to one obvious
managed service. Scale up only when load demands it.

## Why AWS is justified here (not over-engineering)

The indexing pipeline is **long-running and bursty** — a repo import spins up minutes of
parse/embed work, then idles. That's a real reason to run the **worker as an
independently scalable service** behind a queue, separate from the always-on API. This is
the deployment's core story: API and worker scale on different curves.

## Service mapping

| component | AWS service | notes |
|-----------|-------------|-------|
| api (FastAPI) | **ECS Fargate** service | behind an **ALB**; autoscale on CPU/req |
| worker (Celery) | **ECS Fargate** service | separate service; autoscale on queue depth |
| Postgres + pgvector | **RDS for PostgreSQL** | RDS supports the `pgvector` extension |
| Redis (broker) | **ElastiCache for Redis** | Celery broker + status |
| cloned repos / ZIP uploads | **S3** | ephemeral clone artifacts / uploads |
| secrets, tokens, API keys | **Secrets Manager** (or SSM Parameter Store) | injected into tasks |
| container images | **ECR** | pushed by CI |
| frontend (React build) | **S3 + CloudFront** | static hosting + CDN (Vercel is a fine alternative) |
| DNS / TLS | **Route 53 + ACM** | domain + certs |

Same container image for `api` and `worker`, different entrypoints — build once, run two
services.

## Phased rollout

**Phase 1 — launch (simplest thing that works).**
Single EC2 (or Lightsail) running the `docker-compose` stack, or Fargate api/worker +
RDS + ElastiCache. Frontend on S3+CloudFront (or Vercel). One environment. Ship, get it
working end-to-end, learn the real load shape.

**Phase 2 — scale when it hurts.**
Split api/worker into separate Fargate services with independent autoscaling (worker on
queue depth). Move clone storage to S3. Secrets to Secrets Manager. Add a staging
environment. This is where the independent-scaling story becomes real.

**Deliberately deferred (and say so):** Kubernetes/EKS, Lambda, service mesh,
multi-region. None are warranted at this stage — reaching for them early is the
anti-pattern, not the flex. Add only if scale actually demands it.

## CI/CD

GitHub Actions: on merge to main → build images → push to ECR → deploy the ECS services
(`aws ecs update-service` / a deploy action). Run Alembic migrations as a one-off ECS
task before rolling the api service.

## Operational notes

- `CLONE_STORAGE_DIR` → an S3-backed flow or an ephemeral Fargate volume; clones are
  disposable, so don't treat them as durable state.
- RDS is the only durable store — enable automated backups.
- Worker tasks are idempotent where possible; re-indexing by `content_hash` already
  supports safe retries.
- Health checks: ALB → api `/health`; worker liveness via Celery ping.
