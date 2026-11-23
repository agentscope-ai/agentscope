# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for K8s resource identity and legacy workspace recovery."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, skipIf
from unittest.mock import AsyncMock, Mock, call, patch

try:
    from kubernetes_asyncio import client as k8s_client
except ImportError:
    k8s_client = None

from agentscope.workspace import K8sWorkspace
from agentscope.workspace._k8s._constants import (
    _k8s_legacy_name,
    _k8s_safe_name,
)


class K8sSafeNameTest(TestCase):
    """Tests for :func:`_k8s_safe_name`."""

    def test_names_include_original_id_digest(self) -> None:
        """Plain, lossy, and case-variant IDs have distinct names."""
        self.assertEqual(
            {
                wid: _k8s_safe_name(wid)
                for wid in ("reports", "Reports", "a.b", "a_b", "a-b-2e7336dc")
            },
            {
                "reports": "as-ws-reports-7f26104f77a7ce47",
                "Reports": "as-ws-reports-dacca3cba3f346a4",
                "a.b": "as-ws-a-b-2e7336dc8eba87ef",
                "a_b": "as-ws-a-b-648fa9b31bc7ff7e",
                "a-b-2e7336dc": "as-ws-a-b-2e7336dc-e2ae217fec562ab0",
            },
        )

    def test_plain_id_cannot_copy_a_hashed_name(self) -> None:
        """A generated suffix is hashed again when used as a plain ID."""
        name = _k8s_safe_name("a.b")
        self.assertNotEqual(name, _k8s_safe_name(name.removeprefix("as-ws-")))

    def test_legacy_names_are_preserved_for_lookup(self) -> None:
        """Recover the exact names used before workspace ID hashing."""
        self.assertEqual(
            [_k8s_legacy_name(wid) for wid in ("Reports", "a_b", "x" * 80)],
            ["as-ws-reports", "as-ws-a-b", "as-ws-" + "x" * 57],
        )

    def test_name_is_rfc1123_compliant(self) -> None:
        """Result stays RFC-1123 compliant for lossy ids."""
        for wid in (
            "a" * 200,
            "\u4e2d\u6587",
            "A.B_C",
            "  ",
            "---",
            "workspace-" + "x" * 60 + "-aaa",
        ):
            name = _k8s_safe_name(wid)
            self.assertLessEqual(len(name), 63)
            self.assertRegex(
                name,
                r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
            )
            self.assertFalse(name.endswith("-"))

    def test_long_ids_sharing_a_prefix_do_not_collide(self) -> None:
        """Distinct long ids sharing a prefix need distinct Pod names."""
        left = "workspace-" + "x" * 60 + "-aaa"
        right = "workspace-" + "x" * 60 + "-bbb"
        self.assertNotEqual(_k8s_safe_name(left), _k8s_safe_name(right))

    def test_ids_differing_in_replaced_chars_do_not_collide(self) -> None:
        """Ids differing only in characters replaced by '-' stay distinct."""
        self.assertNotEqual(
            _k8s_safe_name("\u4e2d\u6587"),
            _k8s_safe_name("\u00c4\u00d6\u00dc"),
        )
        self.assertNotEqual(_k8s_safe_name("a.b"), _k8s_safe_name("a_b"))

    def test_name_is_deterministic(self) -> None:
        """The same id always yields the same Pod name."""
        self.assertEqual(
            _k8s_safe_name("a" * 100),
            _k8s_safe_name("a" * 100),
        )


def _resource(name: str, owner: str | None) -> SimpleNamespace:
    """Build the metadata and status returned by resource reads."""
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            labels={"agentscope.workspace.id": owner} if owner else None,
            annotations=None,
            uid="original-uid",
            deletion_timestamp=None,
        ),
        status=SimpleNamespace(phase="Running"),
    )


@skipIf(k8s_client is None, "requires kubernetes_asyncio")
class K8sWorkspaceIdentityTest(IsolatedAsyncioTestCase):
    """Exercise identity decisions without a Kubernetes cluster."""

    def setUp(self) -> None:
        """Provide an API whose resources are selected by exact name."""
        self.ws = K8sWorkspace(workspace_id="a_b")
        self.name = _k8s_safe_name(self.ws.workspace_id)
        self.legacy = _k8s_legacy_name(self.ws.workspace_id)
        self.resources: dict[tuple[str, str], SimpleNamespace] = {}
        self.api = Mock()
        for kind in ("pod", "persistent_volume_claim"):
            read = AsyncMock(side_effect=self._reader(kind))
            setattr(self.api, f"read_namespaced_{kind}", read)
            setattr(self.api, f"create_namespaced_{kind}", AsyncMock())
            setattr(self.api, f"delete_namespaced_{kind}", AsyncMock())
        self.ws._v1 = self.api
        self.ws._pod_name = self.name

    def _reader(self, kind: str) -> Mock:
        """Read from the fake cluster, returning 404 for absent names."""

        def read(name: str, namespace: str) -> SimpleNamespace:
            self.assertEqual(namespace, "agentscope")
            if (kind, name) not in self.resources:
                raise k8s_client.ApiException(status=404)
            return self.resources[kind, name]

        return Mock(side_effect=read)

    async def test_resource_name_resolution(self) -> None:
        """Select owned current or legacy resources before creating any."""
        cases = (
            ([], self.name),
            ([("pod", self.name, "a_b")], self.name),
            ([("persistent_volume_claim", self.name, "a_b")], self.name),
            ([("pod", self.legacy, "a_b")], self.legacy),
            (
                [("persistent_volume_claim", self.legacy, "a_b")],
                self.legacy,
            ),
            ([("pod", self.legacy, "a.b")], self.name),
            ([("pod", self.legacy, "A_B")], self.name),
        )
        for entries, expected in cases:
            with self.subTest(entries=entries):
                self.resources = {
                    (kind, name): _resource(name, owner)
                    for kind, name, owner in entries
                }
                self.api.reset_mock()
                self.assertEqual(await self.ws._resolve_pod_name(), expected)
                self.assertEqual(
                    self.api.mock_calls,
                    [
                        call.read_namespaced_persistent_volume_claim(
                            self.name,
                            "agentscope",
                        ),
                        call.read_namespaced_pod(self.name, "agentscope"),
                        call.read_namespaced_persistent_volume_claim(
                            self.legacy,
                            "agentscope",
                        ),
                        call.read_namespaced_pod(self.legacy, "agentscope"),
                    ],
                )

    async def test_ambiguous_or_foreign_resources_are_rejected(self) -> None:
        """Never choose between duplicate data or accept mixed ownership."""
        cases = (
            [("pod", self.name, "other")],
            [("persistent_volume_claim", self.name, None)],
            [("persistent_volume_claim", self.legacy, None)],
            [
                ("pod", self.legacy, "other"),
                ("persistent_volume_claim", self.legacy, "a_b"),
            ],
            [
                ("pod", self.name, "a_b"),
                ("persistent_volume_claim", self.legacy, "a_b"),
            ],
        )
        for entries in cases:
            with self.subTest(entries=entries):
                self.resources = {
                    (kind, name): _resource(name, owner)
                    for kind, name, owner in entries
                }
                with self.assertRaisesRegex(
                    RuntimeError,
                    "not owned|both current and legacy",
                ):
                    await self.ws._resolve_pod_name()
        self.api.create_namespaced_pod.assert_not_awaited()
        self.api.create_namespaced_persistent_volume_claim.assert_not_awaited()

    async def test_lookup_errors_do_not_create_new_storage(self) -> None:
        """An inaccessible legacy resource must not look like absence."""
        self.api.read_namespaced_persistent_volume_claim.side_effect = [
            k8s_client.ApiException(status=404),
            k8s_client.ApiException(status=403),
        ]
        with self.assertRaises(k8s_client.ApiException) as caught:
            await self.ws._resolve_pod_name()
        self.assertEqual(caught.exception.status, 403)
        self.api.create_namespaced_persistent_volume_claim.assert_not_awaited()

    async def test_provision_reuses_legacy_pvc(self) -> None:
        """A restart after upgrade mounts the old PVC in the new Pod."""
        self.resources["persistent_volume_claim", self.legacy] = _resource(
            self.legacy,
            "a_b",
        )
        self.ws._ensure_namespace = AsyncMock()
        self.ws._wait_pod_running = AsyncMock()
        api_client = AsyncMock()
        with (
            patch("kubernetes_asyncio.config.load_incluster_config"),
            patch(
                "kubernetes_asyncio.client.ApiClient",
                return_value=api_client,
            ),
            patch(
                "kubernetes_asyncio.client.CoreV1Api",
                return_value=self.api,
            ),
        ):
            await self.ws._provision_backend()
        self.api.create_namespaced_persistent_volume_claim.assert_not_awaited()
        self.api.create_namespaced_pod.assert_awaited_once()
        namespace, pod = self.api.create_namespaced_pod.call_args.args
        self.assertEqual(
            {
                "namespace": namespace,
                "pod": pod.metadata.name,
                "owner": pod.metadata.annotations["agentscope.workspace.id"],
                "pvc": pod.spec.volumes[0].persistent_volume_claim.claim_name,
                "backend_pod": self.ws._backend._pod_name,
            },
            {
                "namespace": "agentscope",
                "pod": self.legacy,
                "owner": "a_b",
                "pvc": self.legacy,
                "backend_pod": self.legacy,
            },
        )

    async def test_ensure_rechecks_resource_ownership(self) -> None:
        """Reject resources replaced after name resolution."""
        for owner in ("other", None):
            for phase in ("Running", "Failed"):
                with self.subTest(owner=owner, phase=phase):
                    resource = _resource(self.name, owner)
                    resource.status.phase = phase
                    self.resources = {
                        (kind, self.name): resource
                        for kind in ("pod", "persistent_volume_claim")
                    }
                    for ensure in (self.ws._ensure_pvc, self.ws._ensure_pod):
                        with self.assertRaisesRegex(RuntimeError, "not owned"):
                            await ensure()
        for kind in ("pod", "persistent_volume_claim"):
            getattr(self.api, f"create_namespaced_{kind}").assert_not_awaited()
            getattr(self.api, f"delete_namespaced_{kind}").assert_not_awaited()

    async def test_creation_conflicts_are_not_adopted(self) -> None:
        """A competing, possibly deleting resource must not be adopted."""
        for kind in ("pod", "persistent_volume_claim"):
            with self.subTest(kind=kind):
                create = getattr(self.api, f"create_namespaced_{kind}")
                conflict = k8s_client.ApiException(status=409)
                create.side_effect = conflict
                read = getattr(self.api, f"read_namespaced_{kind}")
                resource = _resource(self.name, "a_b")
                resource.metadata.deletion_timestamp = "2026-11-17T00:00:00Z"
                read.side_effect = [
                    k8s_client.ApiException(status=404),
                    resource,
                ]
                ensure = (
                    self.ws._ensure_pod
                    if kind == "pod"
                    else self.ws._ensure_pvc
                )
                with self.assertRaises(k8s_client.ApiException) as caught:
                    await ensure()
                self.assertIs(caught.exception, conflict)
                self.assertEqual(
                    read.await_args_list,
                    [call(self.name, "agentscope")],
                )

    async def test_teardown_checks_owner_and_resolved_name(self) -> None:
        """Delete only owned resources at the selected legacy name."""
        pod_delete = self.api.delete_namespaced_pod
        pvc_delete = self.api.delete_namespaced_persistent_volume_claim
        expected_delete = call(
            self.legacy,
            "agentscope",
            body=k8s_client.V1DeleteOptions(
                preconditions=k8s_client.V1Preconditions(uid="original-uid"),
            ),
        )
        for owner in ("a_b", "other", None):
            for delete_pvc in (True, False):
                with self.subTest(owner=owner, delete_pvc=delete_pvc):
                    self.ws._delete_pvc_on_close = delete_pvc
                    self.ws._pod_name = self.legacy
                    self.ws._v1 = self.api
                    self.ws._api_client = AsyncMock()
                    api_client = self.ws._api_client
                    self.api.reset_mock()
                    self.resources = {
                        (kind, self.legacy): _resource(self.legacy, owner)
                        for kind in ("pod", "persistent_volume_claim")
                    }
                    await self.ws._teardown_backend()
                    self.assertEqual(
                        {
                            "pod": pod_delete.await_args_list,
                            "pvc": pvc_delete.await_args_list,
                        },
                        {
                            "pod": [expected_delete] if owner == "a_b" else [],
                            "pvc": [expected_delete]
                            if owner == "a_b" and delete_pvc
                            else [],
                        },
                    )
                    api_client.close.assert_awaited_once_with()
                    self.assertEqual(
                        (self.ws._v1, self.ws._api_client),
                        (None, None),
                    )

    async def test_pod_wait_rechecks_owner(self) -> None:
        """A Running Pod must still belong to this workspace."""
        for owner in ("a_b", "other"):
            with self.subTest(owner=owner):
                self.resources["pod", self.name] = _resource(self.name, owner)
                if owner == "a_b":
                    await self.ws._wait_pod_running()
                else:
                    with self.assertRaisesRegex(RuntimeError, "not owned"):
                        await self.ws._wait_pod_running()

    def test_annotation_takes_precedence_over_legacy_label(self) -> None:
        """Read new identities without losing legacy label compatibility."""
        cases = (
            (None, "a_b", "a_b"),
            ({}, "a_b", "a_b"),
            ({"agentscope.workspace.id": "a_b"}, None, "a_b"),
            ({"agentscope.workspace.id": "other"}, "a_b", "other"),
            ({"agentscope.workspace.id": ""}, "a_b", ""),
            (None, None, None),
        )
        actual = []
        for annotations, label, _ in cases:
            resource = _resource(self.name, label)
            resource.metadata.annotations = annotations
            actual.append(self.ws._resource_owner(resource))
        self.assertEqual(actual, [owner for _, _, owner in cases])
        self.assertIsNone(
            self.ws._resource_owner(SimpleNamespace(metadata=None)),
        )

    async def test_created_resources_keep_full_id_in_annotations(self) -> None:
        """Long and Unicode IDs must not become invalid label values."""
        for wid in ("reports", "a.b", "a_b", "a" * 64, "\u4e2d\u6587"):
            with self.subTest(workspace_id=wid):
                self.ws.workspace_id = wid
                self.ws._pod_name = _k8s_safe_name(wid)
                await self.ws._create_pvc(self.ws._pod_name)
                await self.ws._create_pod()
                actual = []
                for kind in ("persistent_volume_claim", "pod"):
                    create = getattr(self.api, f"create_namespaced_{kind}")
                    namespace, resource = create.call_args.args
                    actual.append(
                        {
                            "namespace": namespace,
                            "name": resource.metadata.name,
                            "labels": resource.metadata.labels,
                            "annotations": resource.metadata.annotations,
                            "owner": self.ws._resource_owner(resource),
                        },
                    )
                    self.assertLessEqual(len(resource.metadata.name), 63)
                    self.assertRegex(
                        resource.metadata.name,
                        r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
                    )
                    self.resources[kind, self.ws._pod_name] = resource
                expected = {
                    "namespace": "agentscope",
                    "name": self.ws._pod_name,
                    "annotations": {"agentscope.workspace.id": wid},
                    "owner": wid,
                }
                self.assertEqual(
                    actual,
                    [
                        {
                            **expected,
                            "labels": {
                                "app.kubernetes.io/managed-by": "agentscope",
                            },
                        },
                        {
                            **expected,
                            "labels": {
                                "app.kubernetes.io/managed-by": "agentscope",
                                "agentscope.workspace": "true",
                            },
                        },
                    ],
                )
                self.assertEqual(
                    await self.ws._resolve_pod_name(),
                    self.ws._pod_name,
                )

    async def test_rebuild_uses_uid_preconditions(self) -> None:
        """A failed or unexpected Pod must be deleted by its exact UID."""
        self.ws._wait_pod_deleted = AsyncMock()
        for phase in ("Failed", None):
            with self.subTest(phase=phase):
                resource = _resource(self.name, "a_b")
                resource.status.phase = phase
                self.resources["pod", self.name] = resource
                self.api.reset_mock()
                await self.ws._ensure_pod()
                self.api.delete_namespaced_pod.assert_awaited_once_with(
                    self.name,
                    "agentscope",
                    body=k8s_client.V1DeleteOptions(
                        preconditions=k8s_client.V1Preconditions(
                            uid="original-uid",
                        ),
                    ),
                )
                self.api.create_namespaced_pod.assert_awaited_once()

    async def test_rebuild_stops_when_uid_has_changed(self) -> None:
        """Do not wait for or recreate a resource after a UID conflict."""
        resource = _resource(self.name, "a_b")
        self.resources["pod", self.name] = resource
        self.ws._wait_pod_deleted = AsyncMock()
        conflict = k8s_client.ApiException(status=409)
        self.api.delete_namespaced_pod.side_effect = conflict
        for phase in ("Failed", None):
            with self.subTest(phase=phase):
                resource.status.phase = phase
                with self.assertRaises(k8s_client.ApiException) as caught:
                    await self.ws._ensure_pod()
                self.assertIs(caught.exception, conflict)
        self.ws._wait_pod_deleted.assert_not_awaited()
        self.api.create_namespaced_pod.assert_not_awaited()
