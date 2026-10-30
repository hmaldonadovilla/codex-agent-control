# Azure DevOps connection and service hooks

An existing pipeline runs CI as usual. A **service hook**, configured in project
settings, posts successful-build events to Agent Desk. No pipeline YAML, deployment
stage or model invocation needs to be added to your repositories.

## Read permissions and identity

In Connections, set the organization name, project name and allowed repository
names/IDs. Supply an ADO personal access token through the local interface. The
application stores it only in private `.local/secrets.json`.

The review credential needs read access to the project, repository/PR metadata,
builds and policy evaluations. Common PAT selections are Code **Read**, Build
**Read** and Project and Team **Read**, together with the account's project/repository
permissions. It does not need code, work-item or deployment write access for review.
Organization policy may require an administrator to approve access.

Use **Test ADO**. This reads the project and authenticated identity; it also records
that identity for the default own-author exclusion. Add other accounts used by your
team's own agents to the excluded-author list if needed. A successful identity test
does not prove that every repository/build/policy read is permitted; later errors
identify the failing operation and HTTP status without exposing response bodies.

## Required PR build policy

Configure required build validation for each target branch you want automatically
reviewed. Agent Desk requires:

- An active, non-draft PR by a non-excluded author in the selected repositories.
- At least one enabled, blocking **build-validation** policy.
- Every such policy approved, with a completed successful build for the current
  source SHA or PR merge SHA.
- Unchanged source and target revisions after validation and before native delivery.

Reviewer votes, comment resolution and merge strategy are separate merge gates;
they are not mistaken for CI build requirements. A documentation PR without a required
build policy is held for manual review rather than treated as automatically qualified.

## Register the service hook

First complete the [dedicated dev tunnel setup](azure-dev-tunnels.md). The receiver is:

```text
https://<port-specific-tunnel-host>/webhooks/ado
```

### From Agent Desk

**Connections → Connect successful ADO build events** creates one project-scoped,
authenticated `build.complete` subscription filtered to `Succeeded`. This explicit
setup operation needs service-hook creation permission in addition to review reads.
It is not performed by merely enabling reviews.

The app reuses an identical subscription for the same receiver. It refuses conflicting
or duplicate receiver definitions and never edits unrelated subscriptions. If the
review PAT lacks creation permission, use the manual administrator path below instead
of broadening the normal reviewer credential unnecessarily.

### Manually in ADO

Following [Microsoft's webhook guide](https://learn.microsoft.com/en-us/azure/devops/service-hooks/services/webhooks?view=azure-devops):

1. Open **Project settings → Service hooks → Create subscription → Web Hooks**.
2. Choose **Build completed**, with status **Succeeded**. Leave branch unrestricted
   so PR validation refs are delivered; selecting only `main` would send merge builds,
   which Agent Desk deliberately ignores. A pipeline filter is optional.
3. Set the HTTPS receiver URL above.
4. In Agent Desk, explicitly open **Connections → Show webhook setup**. Copy the
   private webhook credential into the hook's **Basic authentication password**;
   use `ado` as username. This password is distinct from the ADO PAT. Never include
   it in a URL, screenshot, issue or pipeline variable log.
5. Choose **Minimal** resource details, **None** messages and **None** detailed messages.
   Minimal must retain the build ID; choosing no resource data cannot be routed.
6. Use **Test**, inspect the HTTP result and save the subscription. Confirm the local
   Activity log receives the event. Test delivery alone does not prove that a real
   PR qualifies or that Codex finished a review.

HTTPS is required when using Basic authentication. See Microsoft's
[programmatic subscription guide](https://learn.microsoft.com/en-us/azure/devops/service-hooks/create-subscription?view=azure-devops)
for administration outside the UI. Agent Desk's generated subscription uses resource
version `2.0`; the handler relies on the event and resource identifiers, then reads ADO.

## Routing and duplicate protection

A normalized build notification retains only its event ID, type and build ID, for
example:

```json
{
  "id": "unique-event-id",
  "eventType": "build.complete",
  "resource": {"id": 123}
}
```

This is a schema example, not a command to replay into a live instance. An authenticated
payload is bounded to 256 KiB and persisted before HTTP 202. Duplicate event IDs do
not create duplicate work. The controller fetches the real build: automatic review
builds must use `refs/pull/<number>/merge` and match the current PR revision. Main,
release, stale, failed and unrelated builds cannot schedule a review.

The receiver can also accept identified `git.pullrequest.created` and
`git.pullrequest.updated` events. They still require authoritative eligibility and CI;
a completed PR is ineligible. The built-in registration creates only the successful
build hook. Manual **Check ADO now** and explicitly enabled HTTP reconciliation are
the only paths that scan active PRs rather than resolving a specific event.

## Verify a real review

After testing local CLI connectivity and the public route, enable the reviewer and
automation. Use an authorized external-developer PR with a required passing build.
Verify the linked native chat receives its exact run ID after the configured grace
period. A native queue receipt confirms only acceptance; the agent's report confirms
the actual outcome. Do not create fake passing policies or replay confidential events
to manufacture a demonstration.

## Troubleshooting

| Symptom | Meaning / next check |
| --- | --- |
| Identity test 401 | PAT expiry, organization or account sign-in |
| Repository/build/policy read 403 | That PAT scope plus project/repository permissions |
| Hook registration refused | Administrator/service-hook permissions or conflicting receiver definition |
| No review after successful main build | Expected: merge/main builds are ignored |
| PR held after green CI | Required policy, exact SHA, author, draft status, repository and agent scope |
| Review waits briefly | Default 30-second grace; this is not polling |
| PR merged while chat was busy | Start guard reports a skip; no new code analysis is needed |
| Delivery state unknown | Check the original chat before manually reconciling; do not blindly resend |

The workstation and tunnel must be available when events arrive. Inspect ADO's hook
notification history for retries/failures. For durable offline retention beyond ADO's
delivery behavior, an external queue/relay would need a separate implementation.
