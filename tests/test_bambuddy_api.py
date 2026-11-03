"""Offline request contracts and direct HTTP/HTTPS transport tests."""

from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import stat
import ssl
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bambuddy_api as api  # noqa: E402


class BambuddyAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((api.API_DIR / "manifest.json").read_text())
        cls.spec = json.loads((api.API_DIR / cls.manifest["schema_file"]).read_text())

    def cli(self, *args, environment=None):
        output = io.StringIO()
        if environment is None:
            environment = {
                "BAMBUDDY_URL": "https://bambuddy.example",
                "BAMBUDDY_API_KEY": "test-key",
            }
        with patch.dict("os.environ", environment, clear=True), redirect_stdout(output):
            code = api.main(list(args))
        return code, json.loads(output.getvalue())

    def test_discovery_and_description_work_without_a_connection(self):
        with patch.object(
            api, "connection", side_effect=AssertionError("Network setup")
        ):
            _, listing = self.cli("operations", "slice_library_file", environment={})
            self.assertIn(
                "POST /api/v1/library/files/{file_id}/slice",
                [item["selector"] for item in listing["operations"]],
            )
            _, description = self.cli("describe", "slice_library_file", environment={})
            refs = description["referenced_schemas"]
            self.assertIn("#/components/schemas/SliceRequest", refs)
            self.assertIn("#/components/schemas/PresetRef", refs)

    def test_duplicate_ids_preserve_methods_and_require_an_exact_selector(self):
        items = [
            item
            for item in api.operations(self.spec)
            if item["path"] == "/api/v1/printers/{printer_id}/camera/stop"
        ]
        self.assertEqual({item["method"] for item in items}, {"GET", "POST"})
        with self.assertRaisesRegex(api.APIError, "Ambiguous"):
            api.select_operation(self.spec, "stop_camera_stream")
        selected = api.select_operation(
            self.spec, "GET /api/v1/printers/{printer_id}/camera/stop"
        )
        self.assertEqual(selected["method"], "GET")

    def test_path_encoding_repeated_queries_and_parameter_rejection(self):
        item = api.select_operation(self.spec, "get_file")
        path = api.request_path(self.spec, item, [("file_id", "a/b?x=1&y=2")], [])
        self.assertEqual(path, "/api/v1/library/files/a%2Fb%3Fx%3D1%26y%3D2")
        with self.assertRaisesRegex(api.APIError, "Path parameters"):
            api.request_path(self.spec, item, [], [])
        queue = api.select_operation(self.spec, "list_queue")
        path = api.request_path(
            self.spec, queue, [], [("status", "pending"), ("status", "failed")]
        )
        self.assertTrue(path.endswith("?status=pending&status=failed"))
        with self.assertRaisesRegex(api.APIError, "Unknown query"):
            api.request_path(self.spec, queue, [], [("typo", "1")])

    def test_mutation_and_action_get_are_guarded_before_transport(self):
        with patch.object(api, "exchange") as transport:
            with self.assertRaisesRegex(api.APIError, "requires --write"):
                self.cli("call", "add_to_queue", "--json", '{"manual_start":true}')
            with self.assertRaisesRegex(api.APIError, "requires --write"):
                self.cli(
                    "call",
                    "GET /api/v1/printers/{printer_id}/camera/stop",
                    "--path",
                    "printer_id=1",
                )
            transport.assert_not_called()

    def test_dry_run_is_offline_and_masks_body_and_url_tokens(self):
        with patch.object(
            api, "exchange", side_effect=AssertionError("Network request")
        ):
            _, preview = self.cli(
                "call",
                "add_to_queue",
                "--json",
                '{"secret":"private","manual_start":true}',
                "--dry-run",
                environment={},
            )
            self.assertEqual(preview["body"]["secret"], "********")
            self.assertTrue(preview["requires_write"])
            _, preview = self.cli(
                "call",
                "download_library_file_for_slicer",
                "--path",
                "file_id=1",
                "--path",
                "token=private",
                "--path",
                "filename=model.3mf",
                "--dry-run",
            )
            self.assertNotIn("private", preview["path"])

    def test_upload_reads_local_bytes_and_previews_metadata_only(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "test model.stl"
            data = b"solid test\x00\xff\nendsolid test"
            file.write_bytes(data)
            with patch.object(
                api, "exchange", return_value=({"status": 200}, b'{"id":33}')
            ) as transport:
                _, result = self.cli(
                    "call", "upload_file", "--upload", f"file={file}", "--write"
                )
                self.assertEqual(result["id"], 33)
                packet = transport.call_args.args
                self.assertIn(data, packet[3])
                self.assertIn(b'name="file"; filename="test model.stl"', packet[3])
                self.assertTrue(packet[4].startswith("multipart/form-data; boundary="))
            with patch.object(api, "exchange", side_effect=AssertionError("Network")):
                _, preview = self.cli(
                    "call", "upload_file", "--upload", f"file={file}", "--dry-run"
                )
                self.assertEqual(preview["body"]["uploads"][0]["bytes"], len(data))
                self.assertNotIn("solid test", json.dumps(preview))
                with self.assertRaisesRegex(api.APIError, "request body is required"):
                    self.cli("call", "upload_file", "--dry-run")

    def test_json_output_masks_secrets_but_preserves_availability_flags(self):
        raw = b'{"access_code":"private","serial_number":"serial","has_cloud_token":true,"nested":{"api_key":"key"}}'
        with patch.object(api, "exchange", return_value=({"status": 200}, raw)):
            _, value = self.cli("call", "list_printers")
        self.assertEqual(value["access_code"], "********")
        self.assertEqual(value["serial_number"], "********")
        self.assertEqual(value["nested"]["api_key"], "********")
        self.assertIs(value["has_cloud_token"], True)

    def test_json_null_and_empty_response_are_distinct(self):
        with patch.object(api, "exchange", return_value=({"status": 200}, b"null")):
            _, value = self.cli("call", "list_printers")
            self.assertIsNone(value)
        with patch.object(api, "exchange", return_value=({"status": 204}, b"")):
            _, value = self.cli("call", "list_printers")
            self.assertEqual(value, {"http_status": 204})

    def test_binary_output_is_private_and_never_overwrites(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / "model.3mf"
            payload = b"PK\x00\xff\r\n"
            with patch.object(api, "exchange", return_value=({"status": 200}, payload)):
                self.cli(
                    "call",
                    "download_file",
                    "--path",
                    "file_id=1",
                    "--output",
                    str(output),
                )
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with patch.object(api, "exchange") as transport:
                with self.assertRaisesRegex(api.APIError, "already exists"):
                    self.cli(
                        "call",
                        "download_file",
                        "--path",
                        "file_id=1",
                        "--output",
                        str(output),
                    )
                transport.assert_not_called()

    def test_errors_are_nonzero_and_do_not_print_unstructured_responses(self):
        for status, raw in [
            (410, b'{"detail":"legacy route"}'),
            (302, b"<html>private</html>"),
        ]:
            with (
                self.subTest(status=status),
                patch.object(api, "exchange", return_value=({"status": status}, raw)),
            ):
                code, error = self.cli("call", "list_printers")
                self.assertEqual(code, 1)
                self.assertEqual(error["http_status"], status)
                self.assertNotIn("private", json.dumps(error))

    def test_transport_does_not_retry_and_only_sends_key_as_http_header(self):
        client = Mock()
        client.request.side_effect = TimeoutError("private-key")
        with patch.dict("os.environ", {"BAMBUDDY_API_KEY": "private-key"}):
            with patch.object(api.http.client, "HTTPSConnection", return_value=client):
                with self.assertRaisesRegex(api.APIError, "inspect state") as error:
                    api.exchange(
                        api.connection("https://bambuddy.example"),
                        "POST",
                        "/path",
                        b"{}",
                    )
        self.assertNotIn("private-key", str(error.exception))
        client.request.assert_called_once()
        self.assertEqual(
            client.request.call_args.kwargs["headers"]["X-API-Key"], "private-key"
        )
        client.getresponse.assert_not_called()
        client.close.assert_called_once()

    def test_https_verifies_certificates_using_system_trust(self):
        client = Mock()
        response = client.getresponse.return_value
        response.status = 200
        response.read.return_value = b"{}"
        response.getheader.return_value = "application/json"
        with patch.object(
            api.http.client, "HTTPSConnection", return_value=client
        ) as factory:
            api.exchange(
                {
                    "base_url": "https://bambuddy.example",
                    "api_key": "test-key",
                },
                "GET",
                "/health",
            )
        context = factory.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(len(context.get_ca_certs()), 0)

    def test_invalid_tls_certificate_fails_without_a_fallback(self):
        client = Mock()
        client.request.side_effect = ssl.SSLCertVerificationError("invalid certificate")
        with patch.object(api.http.client, "HTTPSConnection", return_value=client):
            with self.assertRaisesRegex(
                api.APIError, "certificate verification failed"
            ):
                api.exchange(
                    {"base_url": "https://bambuddy.example", "api_key": "test-key"},
                    "GET",
                    "/health",
                )
        client.request.assert_called_once()
        client.close.assert_called_once()

    def test_response_limit_closes_the_http_connection(self):
        client = Mock()
        client.getresponse.return_value.read.return_value = b"12345"
        with patch.object(api, "MAX_BYTES", 4):
            with patch.object(api.http.client, "HTTPConnection", return_value=client):
                with self.assertRaisesRegex(api.APIError, "Response exceeds"):
                    api.exchange(
                        {"base_url": "http://127.0.0.1:8000", "api_key": "test-key"},
                        "GET",
                        "/health",
                    )
        client.getresponse.return_value.read.assert_called_once_with(5)
        client.close.assert_called_once()

    def test_ingestion_retains_all_bindings_and_resolves_schemas(self):
        with TemporaryDirectory() as folder:
            result = api.ingest(json.dumps(self.spec).encode(), Path(folder), "test")
            self.assertEqual(result["operations"], len(api.operations(self.spec)))
            self.assertGreater(result["operations"], result["unique_operation_ids"])
            rows = (Path(folder) / "operations.tsv").read_text().splitlines()
            self.assertEqual(len(rows) - 1, result["operations"])
            for method in ("GET", "POST"):
                self.assertTrue(
                    any(
                        row.startswith(
                            method + "\t/api/v1/printers/{printer_id}/camera/stop\t"
                        )
                        for row in rows
                    )
                )

    def test_connection_needs_only_the_instance_url_and_api_key(self):
        values = {
            "BAMBUDDY_URL": "https://bambuddy.example/subpath/",
            "BAMBUDDY_API_KEY": "test-key",
        }
        with patch.dict("os.environ", values, clear=True):
            self.assertEqual(
                api.connection(),
                {"base_url": "https://bambuddy.example/subpath", "api_key": "test-key"},
            )
            self.assertEqual(
                api.connection("http://127.0.0.1:8000")["base_url"],
                "http://127.0.0.1:8000",
            )
        for url in (
            "file:///etc/passwd",
            "https://user:password@bambuddy.example",
            "https://bambuddy.example?token=private",
            "https://bambuddy.example#fragment",
        ):
            with patch.dict("os.environ", values, clear=True):
                with self.assertRaises(api.APIError):
                    api.connection(url)

    def test_missing_url_or_api_key_fails_before_network_access(self):
        for environment in (
            {},
            {"BAMBUDDY_URL": "https://bambuddy.example"},
            {"BAMBUDDY_API_KEY": "test-key"},
        ):
            with patch.object(api, "exchange") as transport:
                with self.assertRaisesRegex(api.APIError, "BAMBUDDY_"):
                    self.cli("call", "list_printers", environment=environment)
                transport.assert_not_called()

    def test_url_flag_overrides_the_environment_without_another_config_input(self):
        with patch.object(
            api, "exchange", return_value=({"status": 200}, b"[]")
        ) as transport:
            self.cli("--url", "https://another.example", "call", "list_printers")
        config = transport.call_args.args[0]
        self.assertEqual(
            config, {"base_url": "https://another.example", "api_key": "test-key"}
        )


class DirectHTTPRoundTripTests(unittest.TestCase):
    def test_direct_http_preserves_request_and_binary_response_without_redirects(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, data, self.headers.get("X-API-Key")))
                self.send_response(302 if self.path.endswith("/redirect") else 200)
                self.send_header("Content-Type", "application/octet-stream")
                if self.path.endswith("/redirect"):
                    self.send_header("Location", "/unexpected")
                self.end_headers()
                self.wfile.write(b"PK\x00\xff\r\n")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = b"solid binary\x00\xff\r\n"
            for path, status in [("/upload", 200), ("/redirect", 302)]:
                with patch.dict(
                    "os.environ",
                    {
                        "BAMBUDDY_URL": f"http://127.0.0.1:{server.server_port}/bambuddy/",
                        "BAMBUDDY_API_KEY": "test-key",
                    },
                    clear=True,
                ):
                    response, body = api.exchange(
                        api.connection(),
                        "POST",
                        path,
                        payload,
                        "application/octet-stream",
                        timeout=3,
                    )
                self.assertEqual(response["status"], status)
                self.assertEqual(body, b"PK\x00\xff\r\n")
            self.assertEqual(
                received,
                [
                    ("/bambuddy/upload", payload, "test-key"),
                    ("/bambuddy/redirect", payload, "test-key"),
                ],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
