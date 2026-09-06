# Deployment (AWS — one EC2 host, provisioned by Terraform)

The production shape is the **same `docker-compose.yml` that runs locally**, on a single EC2
instance, with every AWS resource declared in **Terraform**. Right-sized on purpose: V1 load
is a handful of users and bursty indexing, which one box absorbs. Scale out when it hurts,
not before.

## Why this shape

- **Zero drift between dev and prod.** One Compose file, one set of images, one `.env`
  contract. A bug that reproduces locally reproduces on the box.
- **One bill, one thing to reason about.** No ALB, no ECS task definitions, no ElastiCache —
  Postgres and Redis are containers on the same host with an EBS volume under them.
- **Terraform, not console clicks.** The instance, network, IAM, DNS, and storage are code in
  the repo: reviewable in a PR, reproducible in a new account, destroyable with one command.

**Deliberately deferred:** ECS/Fargate, EKS, Lambda, RDS, ElastiCache, multi-AZ. The trigger
for revisiting is concrete — worker queue depth or API latency that a single instance can't
absorb. When that happens, the first two moves are: Postgres → RDS (it's the only durable
state), then the worker → its own instance or ECS service, because `api` and `worker` scale
on different curves.

## What runs where

| component | where | notes |
|---|---|---|
| `api` (FastAPI + LangGraph agent) | container on the EC2 host | `uvicorn`, port 8000 internal |
| `worker` (Celery) | container on the EC2 host | same image as `api`, different entrypoint |
| `db` (Postgres + pgvector) | container on the EC2 host | data dir on an **EBS volume** |
| `redis` | container on the EC2 host | broker + index lock; no persistence needed |
| `web` (React build) | static files served by **Caddy** on the host | Caddy also terminates TLS (Let's Encrypt, automatic) and reverse-proxies `/api` → `api` |
| cloned repos (`CLONE_STORAGE_DIR`) | the same EBS volume | disposable — never durable state |
| secrets (`.env`) | **SSM Parameter Store** (SecureString) | written to `.env` at boot by `user_data`; never baked into an image or AMI |
| container images | **ECR** | pushed by CI; the host pulls with its instance role |
| DNS / IP | **Route 53** record → **Elastic IP** | a stable IP survives instance replacement |

## What Terraform owns (`infra/terraform/`, to be created)

- **Network:** VPC, one public subnet, security group — `443`/`80` open, `22` restricted to
  an allow-listed CIDR (or SSM Session Manager instead of SSH entirely).
- **Identity:** an IAM instance role with ECR pull + SSM Parameter Store read. No static AWS
  keys on the box.
- **Compute + storage:** the EC2 instance (Amazon Linux 2023 or Ubuntu LTS), an EBS volume
  mounted at the Compose data path, an Elastic IP.
- **DNS:** the Route 53 A record.
- **Bootstrap (`user_data`):** install Docker + Compose plugin, mount the EBS volume, log in
  to ECR, fetch secrets from SSM into `.env`, `docker compose pull && docker compose up -d`,
  then `docker compose exec api alembic upgrade head`.
- **State:** S3 backend with DynamoDB locking, so two people can't apply at once.

## CI/CD

GitHub Actions on merge to `main`: build the backend image and the frontend bundle → push the
image to ECR → connect to the host (SSM `send-command`, or SSH) and run
`docker compose pull && docker compose up -d`, followed by the one-off
`alembic upgrade head`. Migrations run before the new `api` takes traffic.

## Operational notes

- **Backups:** scheduled EBS snapshots (Data Lifecycle Manager) cover Postgres and the clone
  volume in one go; clones are disposable, the database is not.
- **Health:** `/health` on `api` behind Caddy; a Route 53 health check or an external uptime
  monitor. Worker liveness via `celery inspect ping`.
- **Restarts:** Compose services carry `restart: unless-stopped` so a reboot brings the
  stack back without intervention.
- **Idempotent work:** worker tasks re-index by `content_hash` and embed `WHERE embedding IS
  NULL`, so an interrupted job resumes on retry rather than starting over.
- **Sizing:** start with a small general-purpose instance and a modest EBS volume; the
  embedding stage is network-bound, not CPU-bound, and Tree-sitter parsing is the only
  CPU-heavy step.
