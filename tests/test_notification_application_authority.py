from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.domain.application import ApplicationRelease,RuntimeSelection
from adaos.domain.application_access import ApplicationPermissionProfile,normalize_application_roles
from adaos.domain.artifact_release import ArtifactSourceRef,ArtifactPackageRef,ProjectRelease
from adaos.domain.notifications import compile_manifest
from adaos.sdk import notifications
from adaos.services.applications import ApplicationService,ApplicationStore,ApplicationAccessService
from adaos.services.notifications.sdk_runtime import NotificationSDKRuntime
from adaos.services import conversation_delivery
from adaos.services.policy.application import bind_application
from test_application_access_service import _application
from test_sdk_notifications import sdk_notification_scope,values
from test_notification_delivery import configure,Adapter


@pytest.fixture
def admitted_notification_application(sdk_notification_scope):
    ctx,root,context=sdk_notification_scope
    ctx.config=SimpleNamespace(subnet_id="A")
    declaration=yaml.safe_load((root/"notifications/manifest.yaml").read_text(encoding="utf8"))
    profile=ApplicationPermissionProfile.from_mapping({"schema":"adaos.application.permission_profile.v1",
        "required":[{"id":"notifications.send","purpose":"Publish owned completed facts"},
                    {"id":"notifications.manage","purpose":"Manage the verified user's attention"}],
        "optional":[],"notifications":compile_manifest(declaration)["permission_disclosure"]})
    roles=normalize_application_roles([{"id":"publisher","title":"Publisher",
        "grants":["notifications.send","notifications.manage"],"requires_permissions":list(profile.flat_permissions),
        "assignable_to":["owner"]}],known_permissions=profile.flat_permissions)
    source=ArtifactSourceRef(forge="github",repository="inimatic/notification-fixture",revision="1"*40,path_scope=("skills/reviewer/",))
    component=ArtifactPackageRef(kind="skill",artifact_id="reviewer",version="1.0.0",digest="sha256:"+"a"*64,
        manifest_digest="sha256:"+"b"*64,source_ref=source)
    project=ProjectRelease(project_id="family_tasks",version="1.0.0",source_ref=source,
        components=(component,),permissions=profile.flat_permissions,validation_evidence=({"status":"passed"},)).seal()
    store=ApplicationStore(ctx.paths.state_dir())
    management=ApplicationService(store)
    management.register(_application())
    release=management.register_release(ApplicationRelease(application_id="family_tasks",publisher_ref="subnet:home",
        project_release=project,accepted_candidate_id="candidate.notifications",acceptance_evidence=({"status":"accepted"},),
        provenance_refs=("sha256:"+"c"*64,),permission_profile=profile,application_roles=roles,lifecycle="trial"))
    access=ApplicationAccessService(management)
    grant=access.grant_access("family_tasks",release_digest=release.release_digest,subject_ref=context["subject_ref"],
        application_roles=("publisher",),permission_ceiling=profile.flat_permissions,issuer_ref=context["subject_ref"],
        idempotency_key="admit-notification-fixture",constraints={"platform_role":"owner","webspace_id":"desktop"})
    selection=RuntimeSelection(webspace_id="desktop",application_id="family_tasks",source="local_trial",
        runtime_root_ref="trial:notifications",release_digest=release.release_digest,revision=1)
    store.save_runtime_selection(selection,expected_revision=0)
    actual={**context,"application_id":"family_tasks","runtime_source":"trial","runtime_root_ref":selection.runtime_root_ref,
        "application_title":"Family Tasks",
        "release_digest":release.release_digest,"permission_profile_digest":profile.digest,
        "webspace_id":"desktop","app_capability":"notifications.send"}
    bind_application(actual)
    runtime=NotificationSDKRuntime()
    binding=runtime.producer()
    binding,_,_=configure(runtime.service,binding,transports=("max",))
    return runtime,access,grant,store,selection,actual


def test_working_beta_uses_real_release_grant_and_revocation_stops_queued_send(admitted_notification_application):
    runtime,access,grant,_,_,context=admitted_notification_application
    captured=notifications.capture_publisher()
    first=notifications.emit(**values())
    adapter=Adapter()
    assert conversation_delivery.dispatch_once(adapter)
    assert len(adapter.sent)==1
    assert runtime.service.get(context["subject_ref"],first["notification_id"])["namespace"]=="trial:notifications"
    second=values();second.update(event_id="E2",source_revision=2)
    notifications.emit(**second)
    access.revoke_access(grant.grant_id,issuer_ref=context["subject_ref"],expected_revision=grant.revision)
    assert conversation_delivery.dispatch_once(adapter)
    assert len(adapter.sent)==1
    with pytest.raises(PermissionError):notifications.emit(**{**second,"event_id":"E3","source_revision":3})
    with pytest.raises(PermissionError):notifications.emit_captured(captured["publisher_ref"],**second)


def test_runtime_retirement_does_not_move_captured_work_to_current_focus(admitted_notification_application):
    runtime,_,_,store,selection,_=admitted_notification_application
    captured=notifications.capture_publisher()
    notifications.emit(**values())
    store.save_runtime_selection(replace(selection,runtime_root_ref="trial:other",revision=2),expected_revision=1)
    adapter=Adapter()
    assert conversation_delivery.dispatch_once(adapter)
    assert adapter.sent==[]
    with pytest.raises(PermissionError):notifications.emit_captured(captured["publisher_ref"],**values())
