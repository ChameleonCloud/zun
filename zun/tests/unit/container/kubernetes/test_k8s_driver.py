import shlex
from unittest import mock
import urllib.parse

from zun.container.k8s import exception as k8s_exc
from zun.container.k8s.driver import K8sDriver, zun_context
from zun.container.k8s.driver import config as k8s_config
from zun.container.k8s.network import K8sNetwork as zun_k8s_network
from zun.objects.container import Container as ZunContainer
from zun.tests.unit.container import base
from zun.conf import CONF
from zun.common import consts

FAKE_PROJECT_ID = "aaaa-bbb-ccc-ddd"


class TestK8sDriver(base.DriverTestCase):
    def setUp(self):
        super().setUp()

        # initialize/mock required config options so the driver can start...
        self.mock_admin_context = mock.patch.object(
            zun_context, "get_admin_context"
        ).start()
        self.mock_zun_k8s_network = mock.patch.object(zun_k8s_network, "init").start()
        self.mock_k8s_config = mock.patch.object(k8s_config, "load_kube_config").start()

        self.driver = K8sDriver()
        self.mock_k8s = mock.MagicMock()

        # mock calls that create may make to the k8s client
        self.mock_get_secrets_for_image = mock.patch.object(
            self.driver, "_get_secrets_for_image"
        ).start()

        self.mock_create_namespaced_deployment = mock.patch.object(
            self.driver.apps_v1, "create_namespaced_deployment"
        ).start()

        self.mock_create_namespaced_network_policy = mock.patch.object(
            self.driver.net_v1, "create_namespaced_network_policy"
        ).start()

    def _ws_client(self, stdout="", fail=False):
        """A finished exec: exit 0, stdout already read, stream closed.

        With fail, running or reading it raises, as when the connection
        drops.
        """
        ws_client = mock.MagicMock(returncode=0, _connected=False,
                                   **{"read_all.return_value": stdout,
                                      "is_open.return_value": False})
        if fail:
            ws_client.run_forever.side_effect = OSError("connection reset")
            ws_client.is_open.side_effect = OSError("connection reset")
        return ws_client


class TestK8sDriverActions(TestK8sDriver):
    def test_create(self):
        """Test container create method.

        This methid implements creating a zun container object, backed by a k8s deployment.
        Returns the zun container object.
        """

        mock_image = mock.MagicMock()

        mock_container = mock.MagicMock(
            spec_set=ZunContainer,
            project_id=FAKE_PROJECT_ID,
            exposed_ports=[],
        )

        result_container = self.driver.create(
            context=self.context,
            container=mock_container,
            image=mock_image,
        )

        # some basic assertions, ensure the project ID gets passed all the way through the stack of calls

        self.assertEqual(result_container.project_id, FAKE_PROJECT_ID)

        self.mock_get_secrets_for_image.assert_called_once()
        self.mock_create_namespaced_deployment.assert_called_once()
        self.assertEqual(
            self.mock_create_namespaced_deployment.call_args[0][0], FAKE_PROJECT_ID
        )

        self.mock_create_namespaced_network_policy.assert_not_called()

    def test_create_exposedports(self):
        mock_image = mock.MagicMock()

        mock_container = mock.MagicMock(
            spec_set=ZunContainer,
            project_id=FAKE_PROJECT_ID,
            exposed_ports=["8000/tcp"],
        )

        result_container = self.driver.create(
            context=self.context,
            container=mock_container,
            image=mock_image,
        )
        self.mock_create_namespaced_network_policy.assert_called_once()
        self.assertEqual(
            self.mock_create_namespaced_network_policy.call_args[0][0], FAKE_PROJECT_ID
        )
        called_spec = self.mock_create_namespaced_network_policy.call_args[0][1]
        ingress_spec = called_spec.get("spec", {}).get("ingress", [])
        self.assertEqual(ingress_spec[0]["ports"], [{"port": 8000, "protocol": "TCP"}])

    def test_commit(self):
        mock_container = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.commit,
            self.context,
            mock_container,
            repository=None,
            tag=None,
        )

    def test_delete(self):
        pass

    def test_show(self):
        pass

    def test_reboot(self):
        pass

    def test_stop(self):
        pass

    def test_start(self):
        pass

    def test_pause(self):
        mock_container = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.pause,
            self.context,
            mock_container,
        )

    def test_unpause(self):
        mock_container = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.unpause,
            self.context,
            mock_container,
        )

    def test_execute_create_deferred_returns_handle(self):
        # run=False must not execute now; it returns an opaque handle for the
        # ExecInstance, and the command runs when a client attaches.
        with mock.patch.object(self.driver, "_connect_pod_exec") as mock_conn:
            handle = self.driver.execute_create(
                self.context, mock.MagicMock(), "ls", run=False)
        self.assertIsInstance(handle, str)
        mock_conn.assert_not_called()

    def test_execute_create_run_executes_synchronously(self):
        # run=True connects, runs to completion, and returns the result that
        # execute_run hands back.
        ws_client = mock.MagicMock()
        ws_client.returncode = 0
        ws_client.read_all.return_value = "hello"
        with mock.patch.object(self.driver, "_connect_pod_exec",
                               return_value=ws_client) as mock_conn:
            result = self.driver.execute_create(
                self.context, mock.MagicMock(), "ls", run=True)
        mock_conn.assert_called_once()
        ws_client.run_forever.assert_called_once()
        self.assertEqual({"output": "hello", "exit_code": 0}, result)

    def test_execute_resize(self):
        # k8s tty resize is handled in-band by the websocket proxy; the driver
        # method is intentionally a no-op.
        self.assertIsNone(
            self.driver.execute_resize(
                exec_id=None, height=None, width=None))

    def _exec_url_params(self, stdin, tty):
        pod = mock.MagicMock()
        pod.metadata.name = "pod"
        pod.metadata.namespace = "project"
        core_v1 = mock.MagicMock()
        core_v1.return_value.api_client.configuration.host = (
            "https://k8s:6443")
        with mock.patch.object(self.driver, "_pod_for_container",
                               return_value=pod), \
                mock.patch.object(self.driver, "core_v1", core_v1):
            url = self.driver.get_exec_url(
                self.context, mock.MagicMock(), "handle", "sh -c 'cat'",
                stdin, tty)
        parts = urllib.parse.urlsplit(url)
        self.assertEqual("wss://k8s:6443/api/v1/namespaces/project/pods/pod"
                         "/exec", url.split("?")[0])
        return urllib.parse.parse_qsl(parts.query)

    def test_get_exec_url_no_stdin_no_tty(self):
        params = self._exec_url_params(stdin=False, tty=False)
        self.assertEqual(
            [("command", "sh"), ("command", "-c"), ("command", "cat"),
             ("tty", "false"), ("stdin", "false"), ("stdout", "true"),
             ("stderr", "true")],
            params)

    def test_get_exec_url_stdin_without_tty(self):
        # stderr stays its own stream when there is no terminal.
        params = self._exec_url_params(stdin=True, tty=False)
        self.assertIn(("stdin", "true"), params)
        self.assertIn(("tty", "false"), params)
        self.assertIn(("stderr", "true"), params)

    def test_get_exec_url_stdin_and_tty(self):
        # With a terminal, k8s merges stderr into stdout.
        params = self._exec_url_params(stdin=True, tty=True)
        self.assertIn(("stdin", "true"), params)
        self.assertIn(("tty", "true"), params)
        self.assertIn(("stderr", "false"), params)

    def test_resize(self):
        mock_container = mock.MagicMock()

        fake_width = 80
        fake_height = 100

        self.assertRaises(
            NotImplementedError,
            self.driver.resize,
            self.context,
            mock_container,
            height=fake_height,
            width=fake_width,
        )

    def test_top(self):
        mock_container = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.top,
            self.context,
            mock_container,
            ps_args=None,
        )

    def test_update(self):
        mock_container = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.update,
            self.context,
            mock_container,
        )

    def test_network_detach(self):
        mock_container = mock.MagicMock()
        mock_network = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.network_detach,
            self.context,
            mock_container,
            network=mock_network,
        )

    def test_network_attach(self):
        mock_container = mock.MagicMock()
        requested_network = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.network_attach,
            self.context,
            mock_container,
            requested_network=requested_network,
        )

    def test_create_network(self):
        network = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.create_network,
            self.context,
            network=network,
        )

    def test_delete_network(self):
        network = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.delete_network,
            self.context,
            network=network,
        )

    def test_inspect_network(self):
        network = mock.MagicMock()

        self.assertRaises(
            NotImplementedError,
            self.driver.inspect_network,
            network=network,
        )


class TestUpdateContainersStates(TestK8sDriver):

    def setUp(self):
        super().setUp()

        # Set "host" so we act as a specific service, used for syncronization checks.
        self.config(host="test-host")


    def test_deletes_when_deployment_missing(self):
        """
        When a k8s zun container IS present, and a k8s deployment is NOT
        Then assume there was a deletion in the background, and delete the zun
        container.

        This is most often triggered by blazar lease end.
        """

        container = mock.MagicMock(
            spec_set=ZunContainer,
            uuid="11111111-1111-1111-1111-111111111111",
            host=CONF.host,
            status=consts.RUNNING,
            task_state=None,
        )

        with mock.patch.object(self.driver, "_deployment_map", return_value={}) as mock_deployment:
            self.driver.update_containers_states(self.context, [container], mock.Mock())
            mock_deployment.assert_called_once()


        self.assertEqual(consts.DELETED, container.status)
        container.save.assert_called_once_with(self.context)

    def test_creating_not_deleted_when_deployment_missing(self):
        """
        Skip deletion if container still "creating", deployment might not be made yet.
        """
        self.config(host="test-host")

        container = mock.MagicMock(
            spec_set=ZunContainer,
            uuid="22222222-2222-2222-2222-222222222222",
            host=CONF.host,
            status=consts.CREATING,
            task_state=None,
        )

        with mock.patch.object(self.driver, "_deployment_map", return_value={}) as mock_deployment:
            self.driver.update_containers_states(self.context, [container], mock.Mock())
            mock_deployment.assert_called_once()

        self.assertEqual(consts.CREATING, container.status)
        container.save.assert_not_called()

    def test_stopped_not_deleted_when_deployment_present(self):
        """
        For a "stopped" zun container, we expect a deployment to be present with scale=0.
        This means that there may be no matching "pod", but there WILL be a matching
        deployment.
        """
        self.config(host="test-host")

        container = mock.MagicMock(
            spec_set=ZunContainer,
            uuid="33333333-3333-3333-3333-333333333333",
            host=CONF.host,
            status=consts.STOPPED,
            task_state=None,
        )

        deployments = {container.uuid: mock.MagicMock()}


        with mock.patch.object(self.driver, "_deployment_map", return_value=deployments) as mock_deployment:
            self.driver.update_containers_states(self.context, [container], mock.Mock())
            mock_deployment.assert_called_once()

        self.assertEqual(consts.STOPPED, container.status)
        container.save.assert_not_called()

    def test_skips_when_task_state_set(self):
        """Ensure we don't delete if task_state != none, k8s still in progress."""
        container = mock.MagicMock(
            spec_set=ZunContainer,
            uuid="44444444-4444-4444-4444-444444444444",
            host=CONF.host,
            status=consts.RUNNING,
            task_state=consts.CONTAINER_CREATING,
        )

        with mock.patch.object(self.driver, "_deployment_map", return_value={}):
            self.driver.update_containers_states(self.context, [container], mock.Mock())

        self.assertEqual(consts.RUNNING, container.status)
        container.save.assert_not_called()

_IMAGE_PULL_STATUSES = (
    "ErrImagePull",
    "InvalidImageName",
    "ImagePullBackOff",
)

class TestSyncContainerImagePullErrors(TestK8sDriver):
    def setUp(self):
        super().setUp()
        self.driver.network_driver = mock.MagicMock()

    def _creating_container(self):
        return mock.MagicMock(
            spec_set=ZunContainer,
            status=consts.CREATING,
            task_state=consts.CONTAINER_CREATING,
        )

    def _pending_pod(self, waiting_reason, waiting_message, image_ref):
        pod = mock.MagicMock()
        pod.status = mock.MagicMock(
            phase="Pending",
            conditions=[
                mock.MagicMock(
                    type="PodScheduled",
                    status="True",
                    reason="Scheduled",
                    message="pod scheduled",
                )
            ],
            container_statuses=[
                mock.MagicMock(
                    state=mock.MagicMock(
                        waiting=mock.MagicMock(
                            reason=waiting_reason,
                            message=waiting_message,
                        )
                    ),
                    restart_count=0,
                )
            ],
            reason=None,
            message=None,
        )
        pod.spec = mock.MagicMock(
            containers=[mock.MagicMock(image=image_ref)],
        )
        return pod

    def test_pending_terminal_image_pull_reasons_fail_fast(self):
        """This is actually complicated...

        We can't set to error, because error implies a terminal case.
        These image statuses are not necessarily terminal.

        Instead, just set status reason and status detail, but leave in creating.
        Separate logic should handle failing due to timeout or retries.
        """
        image_ref = "ghcr.io/chameleoncloud/edge_sensehat_image:latest"
        for waiting_reason in _IMAGE_PULL_STATUSES:
            with self.subTest(waiting_reason=waiting_reason):
                container = self._creating_container()
                pod = self._pending_pod(
                    waiting_reason=waiting_reason,
                    waiting_message="failed to resolve reference: not found",
                    image_ref=image_ref,
                )

                self.driver._sync_container(container, pod)

                # Still creating, but has the status detail and reason
                self.assertEqual(consts.CREATING, container.status)
                self.assertEqual(waiting_reason, container.status_detail)
                self.assertEqual("failed to resolve reference: not found", container.status_reason)


class TestWSClientSelectPatch(base.DriverTestCase):
    """WSClient.update is monkey-patched to avoid select.poll, which
    eventlet.monkey_patch() removes."""

    def test_update_reads_frame_without_select_poll(self):
        import io
        import select
        import socket

        from kubernetes.stream.ws_client import WSClient
        from websocket import ABNF

        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        writer.send(b"x")  # make the socket readable for select.select

        frame = mock.Mock(data=b"\x01hello")
        ws = mock.Mock()
        ws.is_open.return_value = True
        ws.sock.connected = True
        ws.sock.sock = reader
        ws.sock.recv_data_frame.return_value = (ABNF.OPCODE_BINARY, frame)
        ws._all = io.StringIO()
        ws._channels = {}

        # create=True because eventlet's monkey patching has already
        # removed select.poll in the test environment
        with mock.patch.object(select, "poll", create=True) as mock_poll:
            WSClient.update(ws, timeout=1)

        mock_poll.assert_not_called()
        self.assertEqual("hello", ws._all.getvalue())
        self.assertEqual({1: "hello"}, ws._channels)


class TestK8sDriverArchiveArgv(TestK8sDriver):
    """Paths reach the container as single arguments, never through a shell."""

    PATH = "/root/it's \"my\" dir;touch x/$(id) -n.txt"

    def _run(self, operation, *args, ws_clients):
        with mock.patch.object(self.driver, "_connect_pod_exec",
                               side_effect=ws_clients) as connect:
            operation(self.context, mock.MagicMock(), *args)
        # The argv of each command run in the container.
        self.commands = [shlex.split(call.args[2])
                         for call in connect.call_args_list]

    def _get_archive(self, path, is_dir):
        probe = self._ws_client("directory\n" if is_dir else "file\n")
        self._run(self.driver.get_archive, path,
                  ws_clients=[probe, self._ws_client()])

    def test_directory_probe_takes_path_as_argument(self):
        self._get_archive(self.PATH, False)
        probe = self.commands[0]
        self.assertEqual(["sh", "-c"], probe[:2])
        self.assertNotIn("$(id)", probe[2])
        self.assertEqual(["sh", self.PATH], probe[3:])

    def test_get_archive_file(self):
        self._get_archive("-x.txt", False)
        # "./" keeps tar from reading "-x.txt" as an option.
        self.assertEqual(["tar", "cf", "-", "-C", ".", "./-x.txt"],
                         self.commands[1])

    def test_get_archive_directory(self):
        self._get_archive(self.PATH, True)
        self.assertEqual(["tar", "cf", "-", "-C", self.PATH, "."],
                         self.commands[1])

    def test_put_archive(self):
        self._run(self.driver.put_archive, self.PATH, b"data",
                  ws_clients=[self._ws_client()])
        self.assertEqual(["tar", "xmf", "-", "-C", self.PATH],
                         self.commands[0])

    def test_file_by_full_path(self):
        self._get_archive("/etc/hostname", False)
        self.assertEqual(["tar", "cf", "-", "-C", "/etc", "./hostname"],
                         self.commands[1])

    def test_file_by_relative_path(self):
        self._get_archive("logs/app.log", False)
        self.assertEqual(["tar", "cf", "-", "-C", "logs", "./app.log"],
                         self.commands[1])

    def test_file_at_root(self):
        self._get_archive("/init", False)
        self.assertEqual(["tar", "cf", "-", "-C", "/", "./init"],
                         self.commands[1])


class TestK8sDriverClosesExecWebsockets(TestK8sDriver):
    """Every exec websocket is closed, whether or not the operation fails."""

    def _call(self, operation, *args, ws_clients):
        with mock.patch.object(self.driver, "_connect_pod_exec",
                               side_effect=ws_clients):
            return operation(self.context, mock.MagicMock(), *args)

    def assertClosed(self, *ws_clients):
        for ws_client in ws_clients:
            ws_client.close.assert_called_once_with()

    def test_execute(self):
        ws_client = self._ws_client()
        self._call(self.driver.execute_create, "true", ws_clients=[ws_client])
        self.assertClosed(ws_client)

    def test_execute_fails(self):
        ws_client = self._ws_client(fail=True)
        self.assertRaises(OSError, self._call, self.driver.execute_create,
                          "true", ws_clients=[ws_client])
        self.assertClosed(ws_client)

    def test_get_archive(self):
        probe, tar = self._ws_client("file\n"), self._ws_client()
        self._call(self.driver.get_archive, "/f", ws_clients=[probe, tar])
        self.assertClosed(probe, tar)

    def test_get_archive_probe_fails(self):
        probe = self._ws_client(fail=True)
        self.assertRaises(OSError, self._call, self.driver.get_archive, "/f",
                          ws_clients=[probe])
        self.assertClosed(probe)

    def test_get_archive_tar_fails(self):
        probe, tar = self._ws_client("file\n"), self._ws_client(fail=True)
        self.assertRaises(k8s_exc.K8sException, self._call,
                          self.driver.get_archive, "/f",
                          ws_clients=[probe, tar])
        self.assertClosed(probe, tar)

    def test_put_archive(self):
        ws_client = self._ws_client()
        self._call(self.driver.put_archive, "/d", b"data",
                   ws_clients=[ws_client])
        self.assertClosed(ws_client)

    def test_put_archive_fails(self):
        ws_client = self._ws_client(fail=True)
        self.assertRaises(OSError, self._call, self.driver.put_archive, "/d",
                          b"data", ws_clients=[ws_client])
        self.assertClosed(ws_client)


class TestK8sDriverPutArchiveStatus(TestK8sDriver):
    def _put_archive(self, ws_client, path="/d"):
        with mock.patch.object(self.driver, "_connect_pod_exec",
                               return_value=ws_client):
            self.driver.put_archive(self.context, mock.MagicMock(), path,
                                    b"data")

    def test_success(self):
        ws_client = self._ws_client()
        self._put_archive(ws_client)
        ws_client.write_stdin.assert_called_once_with(b"data")

    def test_no_exit_code(self):
        # The status has no exit code, so returncode raises ValueError.
        ws_client = self._ws_client()
        type(ws_client).returncode = mock.PropertyMock(side_effect=ValueError)
        e = self.assertRaises(k8s_exc.K8sException, self._put_archive,
                              ws_client)
        self.assertEqual("tar did not report an exit code", str(e))
        self.assertIsInstance(e.__cause__, ValueError)
        ws_client.close.assert_called_once_with()

    def test_tar_failure(self):
        ws_client = self._ws_client()
        ws_client.returncode = 2
        ws_client.read_stderr.return_value = (
            "tar: /missing: Cannot open: No such file or directory\n")
        e = self.assertRaises(k8s_exc.PutArchiveFailed, self._put_archive,
                              ws_client, "/missing")
        self.assertEqual(
            "tar failed extracting into /missing (exit code 2): "
            "tar: /missing: Cannot open: No such file or directory", str(e))

    def test_still_running_at_timeout(self):
        self.config(archive_timeout=120, group="k8s")
        ws_client = self._ws_client()
        ws_client.is_open.return_value = True
        e = self.assertRaises(k8s_exc.PutArchiveTimeout, self._put_archive,
                              ws_client)
        self.assertEqual("Timed out after 120s extracting into /d", str(e))
        ws_client.close.assert_called_once_with()

    def test_percent_in_path(self):
        ws_client = self._ws_client()
        ws_client.returncode = 2
        ws_client.read_stderr.return_value = ""
        e = self.assertRaises(k8s_exc.PutArchiveFailed, self._put_archive,
                              ws_client, "/100%d")
        self.assertIn("/100%d", str(e))
