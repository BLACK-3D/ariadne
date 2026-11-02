"""Read-only business-agent integration never creates or mutates client state."""
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from ariadne.config import Settings
from ariadne.tools import ToolRegistry
from ariadne.store import Store
from ariadne.client_handoff import ClientHandoffDesk
from ariadne.service_desk_tools import ServiceDeskReadTools

ROOT=Path(__file__).absolute().parents[1]


class ServiceDeskToolTests(unittest.TestCase):
    def setUp(self):
        self.provider=ServiceDeskReadTools(Mock())
        self.reader=Mock(return_value={"schema":1,"revision":7,"items":[],"coverage":"bounded_record_window"})

    def test_unbound_provider_is_not_advertised_and_does_not_initialize_state(self):
        self.assertEqual(self.provider.schemas(),[])
        self.assertFalse(self.provider.status()["ready"])
        with self.assertRaises(ValueError):self.provider.execute("service_desk_read",{"action":"next_actions"})

    def test_binding_requires_trusted_callable_not_a_path_or_url(self):
        for value in (None,{},"http://example.invalid","data/private.sqlite3"):
            with self.subTest(value=value),self.assertRaises(ValueError):self.provider.bind(value)

    def test_only_bounded_read_payload_is_forwarded(self):
        self.provider.bind(self.reader)
        result=self.provider.execute("service_desk_read",{"action":"next_actions","limit":2,"offset":20001})
        self.reader.assert_called_once_with(limit=2,offset=20001)
        self.assertFalse(result["write_authority"])
        self.assertEqual(result["coverage"],"bounded_record_window")
        self.assertEqual(result["revision"],7)
        self.assertTrue(self.provider.status()["ready"])

    def test_default_read_does_not_mutate_returned_source(self):
        original=copy.deepcopy(self.reader.return_value)
        self.provider.bind(self.reader);self.provider.execute("service_desk_read",{"action":"next_actions"})
        self.reader.assert_called_once_with(limit=50,offset=0)
        self.assertEqual(self.reader.return_value,original)

    def test_invalid_or_mutating_actions_are_rejected_before_read(self):
        self.provider.bind(self.reader)
        for value in ({},{"action":"accept_intake_packet"},{"action":"prepare_intake_packet"},
                      {"action":"next_actions","packet_id":"P-private"},{"action":"next_actions","url":"http://example.invalid"},[]):
            with self.subTest(value=value),self.assertRaises(ValueError):self.provider.execute("service_desk_read",value)
        self.reader.assert_not_called()

    def test_strict_integer_bounds(self):
        self.provider.bind(self.reader)
        for name,values in (("limit",[0,51,True,1.0,"1",None]),("offset",[-1,32001,False,1.0,"0",None])):
            for value in values:
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):
                    self.provider.execute("service_desk_read",{"action":"next_actions",name:value})
        self.reader.assert_not_called()

    def test_cancellation_is_checked_before_any_read(self):
        self.provider.bind(self.reader)
        with self.assertRaises(ValueError):self.provider.execute("service_desk_read",{"action":"next_actions"},cancelled=lambda:True)
        self.reader.assert_not_called()

    def test_schema_and_fingerprint_are_passive(self):
        self.provider.bind(self.reader)
        self.assertEqual(self.provider.schemas()[0]["function"]["name"],"service_desk_read")
        self.assertEqual(self.provider.policy("service_desk_read",{}),"auto")
        self.assertTrue(self.provider.fingerprint_material("service_desk_read",{})["read_only"])
        self.reader.assert_not_called()

    def test_actual_registry_read_does_not_create_client_tables_or_missions(self):
        parent=ROOT/"staging";parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="service-read-",dir=parent) as temporary:
            settings=Settings(data_dir=Path(temporary)/"data",api_token="synthetic-only")
            store=Store(settings.data_dir/"ariadne.sqlite3")
            registry=ToolRegistry(settings)
            desk=ClientHandoffDesk(registry,store)
            registry.service_desk.bind(desk.next_actions)
            with store.connect() as db:before=list(db.execute("SELECT name FROM sqlite_master ORDER BY name"));runs=db.execute("SELECT count(*) FROM runs").fetchone()[0]
            with patch("socket.socket",side_effect=AssertionError("No network")),patch("subprocess.Popen",side_effect=AssertionError("No process")):
                result=registry.execute("service_desk_read",{"action":"next_actions"})
            self.assertFalse(result["initialized"])
            self.assertEqual(result["items"],[])
            with store.connect() as db:
                self.assertEqual(list(db.execute("SELECT name FROM sqlite_master ORDER BY name")),before)
                self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0],runs)
            refused=registry.execute("service_desk_read",{"action":"accept"})
            self.assertIn("error",refused)
            self.assertFalse(any(row[0].startswith("client_handoff_") for row in before))


if __name__=="__main__":unittest.main()
