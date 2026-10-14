# Deployment

Stub — add production deployment manifests here.

Planned:

- `Dockerfile.prod` — multi-stage prod image (currently `../Dockerfile` is shared dev image)
- `k8s/` — Kubernetes manifests
- `terraform/` — infra

For now deploy via `../../docker-compose.yml`:

```bash
docker compose up --build -d
docker compose logs -f
```
