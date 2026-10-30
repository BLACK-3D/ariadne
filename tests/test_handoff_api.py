"""Owner API boundaries for an isolated fictional service desk."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from ariadne.config import Settings
from ariadne.engine import Engine
from ariadne.server import Application
from ariadne.store import Store


class NoModel:
    def status(self):
        return {"available": False, "models": []}

    def chat(self, *args, **kwargs):
        raise AssertionError("A handoff draft must not use inference.")


class HandoffAPITests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1]/"runtime/tmp"
        parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix="handoff-api-", dir=parent)
        self.addCleanup(temp.cleanup)
        self.settings = Settings(data_dir=Path(temp.name)/"data", api_token="handoff-test-key")
        self.store = Store(self.settings.data_dir/"ariadne.sqlite3")
        self.engine = Engine(self.settings, self.store, NoModel())
        self.app = Application(self.settings, self.store, self.engine)

    def request(self, path, method="GET", body=None, *, auth=True, origin=None, host="localhost:8080", query=""):
        raw = json.dumps({} if body is None else body).encode()
        env = {"HTTP_HOST":host, "REQUEST_METHOD":method, "PATH_INFO":path,
               "CONTENT_TYPE":"application/json", "CONTENT_LENGTH":str(len(raw)), "wsgi.input":io.BytesIO(raw),
               "QUERY_STRING":query}
        if auth:
            env["HTTP_AUTHORIZATION"]="Bearer handoff-test-key"
        if origin:
            env["HTTP_ORIGIN"]=origin
        response={}
        def start(status,headers):
            response.update(status=int(status.split()[0]),headers=dict(headers))
        payload=b"".join(self.app(env,start))
        response["body"]=json.loads(payload) if response["headers"]["Content-Type"].startswith("application/json") else payload
        return response

    def seed(self, request_id="demo-seed"):
        return self.request("/api/handoff/demo","POST",{
            "request_id":request_id,"expected_revision":self.app.handoff.status()["revision"]})

    def test_all_owner_routes_require_auth_and_same_origin(self):
        endpoints=[("/api/handoff","GET"),("/api/handoff/demo","POST"),
                   ("/api/handoff/record","PUT"),("/api/handoff/review","POST"),
                   ("/api/handoff/export","POST"),("/api/handoff/export/acknowledge","POST")]
        for path,method in endpoints:
            with self.subTest(path=path):
                self.assertEqual(self.request(path,method,auth=False)["status"],401)
                self.assertEqual(self.request(path,method,origin="https://outsider.example")["status"],403)
                self.assertEqual(self.request(path,method,host="outsider.example")["status"],403)
        self.assertEqual(self.app.handoff.status()["revision"],0)

    def test_read_does_not_seed_or_invoke_models_and_mismatched_methods_do_not_write(self):
        with patch("socket.create_connection",side_effect=AssertionError("No external access")), patch("subprocess.Popen",side_effect=AssertionError("No workers")):
            first=self.request("/api/handoff")
            second=self.request("/api/handoff")
        self.assertEqual(first["status"],200)
        self.assertEqual(first["body"]["revision"],0)
        self.assertEqual(second["body"]["revision"],0)
        self.assertEqual(self.request("/api/handoff/demo","GET")["status"],404)
        self.assertEqual(self.request("/api/handoff/record","POST")["status"],404)
        self.assertEqual(self.app.handoff.status()["revision"],0)

    def test_demo_seed_is_explicit_and_keeps_business_ledger_empty(self):
        with self.engine.tools.business.database() as db:
            before={name:db.execute("SELECT count(*) FROM "+name).fetchone()[0] for name in ("contacts","items","journals","journal_lines","requests")}
        with patch("socket.create_connection",side_effect=AssertionError("No external access")), patch("subprocess.Popen",side_effect=AssertionError("No workers")):
            result=self.seed()
        self.assertEqual(result["status"],200,result)
        self.assertGreater(self.app.handoff.status()["revision"],0)
        with self.engine.tools.business.database() as db:
            after={name:db.execute("SELECT count(*) FROM "+name).fetchone()[0] for name in before}
        self.assertEqual(before,after)
        self.assertEqual(self.store.runs(),[])
        self.assertFalse(self.store.setting("wake_preferences",{}).get("enabled",False))

    def test_global_pause_holds_every_mutation_but_allows_passive_inspection(self):
        self.store.set_setting("paused",True)
        for path,method in (("demo","POST"),("record","PUT"),("review","POST"),("export","POST"),("export/acknowledge","POST")):
            with self.subTest(path=path):
                self.assertEqual(self.request("/api/handoff/"+path,method,{})["status"],409)
        self.assertEqual(self.request("/api/handoff")["status"],200)
        self.assertEqual(self.app.handoff.status()["revision"],0)
        self.assertTrue(self.store.setting("paused"))

    def test_bad_revision_unknown_fields_and_stale_revision_fail_without_changes(self):
        for body in ({"request_id":"invalid","expected_revision":True},
                     {"request_id":"invalid","expected_revision":0,"post_to_ledger":True}):
            self.assertEqual(self.request("/api/handoff/demo","POST",body)["status"],400)
        self.assertEqual(self.app.handoff.status()["revision"],0)
        self.assertEqual(self.seed()["status"],200)
        before=self.app.handoff.status()["revision"]
        response=self.request("/api/handoff/demo","POST",{"request_id":"stale","expected_revision":0})
        self.assertEqual(response["status"],409,response)
        self.assertEqual(response["body"]["revision"],before)
        self.assertEqual(self.app.handoff.status()["revision"],before)

    def test_reviewed_packet_flows_through_owner_api_and_verified_download_without_ledger_posting(self):
        self.assertEqual(self.seed()["status"],200)
        for index in range(2):
            status=self.app.handoff.status()
            record=next(row for row in status["records"] if row["status"]=="ready_for_review")
            response=self.request("/api/handoff/review","POST",{
                "request_id":"review-"+str(index),"expected_revision":status["revision"],
                "record_id":record["id"],"content_sha256":record["content_sha256"],"decision":"approve"})
            self.assertEqual(response["status"],200,response)
        state=self.app.handoff.status()
        self.assertEqual(state["summary"]["approved"],2)
        self.assertEqual(state["summary"]["held"],2)
        body={"request_id":"api-packet","expected_revision":state["revision"]}
        response=self.request("/api/handoff/export","POST",body)
        self.assertEqual(response["status"],200,response)
        self.assertEqual(response["body"]["status"],"completed")
        for item in response["body"]["artifacts"]:
            downloaded=self.request("/api/artifacts",query=urlencode({"path":item["path"]}))
            self.assertEqual(downloaded["status"],200)
            self.assertEqual(hashlib.sha256(downloaded["body"]).hexdigest(),item["sha256"])
            self.assertIn("attachment",downloaded["headers"]["Content-Disposition"])
        replay=self.request("/api/handoff/export","POST",body)
        self.assertTrue(replay["body"]["replayed"])
        self.assertEqual(replay["body"]["artifacts"],response["body"]["artifacts"])
        with self.engine.tools.business.database() as db:
            for table in ("contacts","items","journals","journal_lines","requests"):
                self.assertEqual(db.execute("SELECT count(*) FROM "+table).fetchone()[0],0)
        self.assertEqual(self.store.runs(),[])

    def test_fixed_assets_keep_self_only_csp_and_main_document_has_the_desk(self):
        for name,mime in (("handoff.js","text/javascript"),("handoff.css","text/css")):
            response=self.request("/static/"+name,auth=False)
            self.assertEqual(response["status"],200)
            self.assertTrue(response["headers"]["Content-Type"].startswith(mime))
            self.assertEqual(response["body"],(self.app.static/name).read_bytes())
            self.assertIn("script-src 'self'",response["headers"]["Content-Security-Policy"])
        page=self.request("/",auth=False)
        self.assertIn(b'id="view-handoff"',page["body"])
        self.assertIn(b'data-view="handoff"',page["body"])


if __name__=="__main__":
    unittest.main()
