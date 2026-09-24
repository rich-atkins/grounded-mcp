---
title: Deploy Process
tags: [engineering, process]
superseded_by: internal/engineering/deploy-process.md
---

# Deploy Process (2025)

Deploys are run by the release manager on Tuesdays and Thursdays from the
release branch. Merge to `main` does not deploy.

## Rollback

Rollback means redeploying the previous tag by hand and takes about 20
minutes. The release manager decides when to roll back; there is no automatic
trigger. Incidents follow the [[incident-runbook]].

## Freeze windows

No deploys on Fridays at all, and none during the Black Friday freeze (the
last two weeks of November).
