# Milestone 1 rollback

Milestone 1 adds no production runtime and should be delivered as one commit.

```bash
cd /home/ubuntu/link-evidence-helper
docker compose -f infra/docker/compose.milestone1.yml down
git revert --no-edit <MILESTONE_1_COMMIT_SHA>
git status --short
git rev-parse HEAD
curl -fsS http://127.0.0.1:8765/health
```

Do not use `git reset --hard`, delete production volumes, or remove production
SQLite/adapters. Development volumes are isolated and should be removed only
after explicit review.
