# Development and main delivery

`dev2027` is the development list; `main` is the release list. Push and PR
qualification use the same complete test plan for the same source/configuration.
Delivery authorization is separate: only qualified source-list pushes publish.
Documentation-only changes retain the repository's explicit path filters.
Nightly reuse, where enabled, requires successful source and environment evidence;
an absent or expired receipt runs the complete plan again.

`SRC_BRANCH` defaults to `main`. A production Hub owns its revisioned selection;
members inherit it over their authenticated link and retain it while offline.
Developer checkouts remain under developer control. Core and Application receivers
must match the selected source list and pin the exact immutable revision/digest.
Development and main counters advance independently in the last version field;
a manual major increase resets both lower fields to zero.

For Core, **Prepare Core release** creates a release PR from `dev2027`, assigns
the next patch above `main`, synchronizes `pyproject.toml` and `uv.lock`, and
requests qualification of that exact head once. The receiving list assigns its
own next patch; a higher development counter does not replace the main counter.
Repeated preparation against the same receiving head is idempotent. A manual
major increase resets both lower counters to zero. Source-list pushes and PR qualification reject a missing version
increment or inconsistent lock before delivery. Concurrent release PRs must be
prepared again after another version reaches main.
If repository policy disallows bot-created PRs, preparation publishes the exact
prepared branch and a comparison link; an authorized maintainer or coding agent
opens its normal release PR. The maintainer-created PR starts qualification;
preparation does not dispatch duplicate checks in this case. The workflow does
not weaken repository permissions.
Private contract consumers admit `dev2027`, `main` and PR merge refs through
the qualification environment. Both push and PR run the same exact pinned
consumer checks before Root delivery.

`main` is the release source. Promote changes through a pull request to `main`.
Main qualification must succeed before application delivery. Manual qualification
and recovery remain available. CI never creates version-bump commits in response
to ordinary development pushes.

Delivery depends on the component:

- Core: qualify the complete selected source revision, then publish that exact revision to
  Global/RU Root. Their managed node updater prepares slots, checks readiness and
  coordinates hub/member rollout.
- Backend: qualify main contracts, request one immutable image build in the
  infrastructure repository, and deliver it to RU then Global Root through the
  installed storage-aware controller.
- Web client: qualify and publish the exact Hosting bundle to separate main/dev sites.
- Native clients and sidecar: build main release packages; signed update admission
  and device installation keep their existing product-specific controls.
- Infrastructure: validate policy and build/deliver the affected backend graph.
  Terraform/DNS changes keep their existing explicit admission; a source push is
  not an arbitrary infrastructure mutation authorization.
- Documentation, registry and supporting libraries: use the same branches and
  repository-specific validation/publication, with no unrelated service rollout.

GitHub branch protection is enabled where the account plan supports it. Private
repositories on the current GitHub Free organization cannot enforce protected
branches; main-only workflow gates and the independent Root admission still
validate the actual producer/source before deployment.

Root CI uses a dedicated forced-command SSH identity. It has no shell, forwarding,
provider keys or Root service token. Incoming data cannot choose a controller,
storage profile, command, arbitrary destination or mutable image tag. The Root
checks GitHub producer metadata and the immutable build receipt before using its
existing controller. Unresolved writer transactions stay recorded for explicit
reconciliation rather than being blindly repeated.

Release packages use immutable technical GitHub Release assets with the exact
source revision, producer run and content digests. Bootstrap kits are published
separately per platform; these prereleases do not change the public latest tag or
authorize installation. Qualified Automation wheels follow the same rule.
Actions artifacts are short-lived diagnostic copies. Core test steps remain
strict; their source-bound compressed report bundle is also retained in the job
summary, so an Actions storage quota failure cannot erase the test receipt or
turn package delivery into a dependency on diagnostic archive storage.
