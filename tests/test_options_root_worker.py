"""Generated KO child boundaries; never launches a real collector or network."""
import os
from unittest.mock import patch
import unittest

from ariadne.handoff import HandoffConflict
from ariadne.options_eod_worker import _run_collector
import test_options_eod_worker as legacy


class RootWorkerTests(unittest.TestCase):
    launch=legacy.EodWorkerTests.launch

    def setUp(self):
        legacy.EodWorkerTests.setUp(self)
        self.policy=tuple({**value,**({"symbol":"KO","provider":"onclick_ko_eod_v1"} if index==1 else {})} for index,value in enumerate(self.policy))
        self.account={"id":"one-shared-account","state":{"schema":5,"symbol":"KO"},"policy":self.epoch}
        self.owner._account=lambda db:self.account

    def run_worker(self, required=(), profile="KO"):
        return _run_collector(self.workflow,*self.policy,"fixed-test-request",list(required),profile=profile)

    def test_exact_root_profile_dedicated_flag_and_no_credential_environment(self):
        with patch.dict(os.environ,{"BROKER_TOKEN":"do-not-pass","HTTP_PROXY":"do-not-pass","ARIADNE_OPTIONS_EOD_COLLECTOR":"1"}),patch("subprocess.Popen",side_effect=self.launch):
            self.assertTrue(self.run_worker()["unavailable"])
        command,kwargs,_=self.created[0]
        self.assertEqual(command[-2:],["--profile","KO"])
        self.assertEqual(kwargs["env"]["ARIADNE_OPTIONS_ROOT_EOD_COLLECTOR"],"1")
        self.assertEqual(kwargs["env"]["ARIADNE_OFFLINE"],"0")
        for key in ("ARIADNE_OPTIONS_EOD_COLLECTOR","BROKER_TOKEN","HTTP_PROXY"):
            self.assertNotIn(key,kwargs["env"])
        self.assertEqual(list((self.registry.settings.data_dir/"options-ko-collector").iterdir()),[])
        self.assertFalse(self.workflow.processes)

    def test_foreign_root_and_legacy_account_never_launch(self):
        for schema,symbol in ((4,"F"),(5,"F"),(3,"KO")):
            self.account["state"]={"schema":schema,"symbol":symbol}
            with self.subTest(schema=schema,symbol=symbol),patch("subprocess.Popen",side_effect=AssertionError("No source fallback")),self.assertRaises(HandoffConflict):
                self.run_worker()
        self.assertFalse(self.created)

    def test_foreign_required_id_and_unknown_profile_rejected_before_launch(self):
        with patch("subprocess.Popen",side_effect=AssertionError("No unregistered request")):
            for required in (["F261113P00010000"],["INTC261113P00060000"],["KO1261113P00092000"]):
                with self.subTest(required=required),self.assertRaises(ValueError):
                    self.run_worker(required)
            self.assertRaises(ValueError,self.run_worker,profile="INTC")

    def test_required_actual_ko_contract_remains_on_the_fixed_child_command(self):
        identifier="KO261113P00092000"
        with patch("subprocess.Popen",side_effect=self.launch):
            self.run_worker([identifier])
        self.assertEqual(self.created[0][0][-2:],["--required-contract",identifier])

    def test_prior_uncertain_ko_scratch_is_never_overwritten_or_replayed(self):
        folder=self.registry.settings.data_dir/"options-ko-collector"
        folder.mkdir(parents=True)
        target=folder/"fixed-test-request.json"
        target.write_bytes(b"uncertain source evidence")
        with patch("subprocess.Popen",side_effect=AssertionError("No replay")),self.assertRaises(FileExistsError):
            self.run_worker()
        self.assertEqual(target.read_bytes(),b"uncertain source evidence")

    def test_permissions_revoked_during_root_child_kill_it_without_adoption(self):
        calls=0
        killed=[]
        def policy(db):
            nonlocal calls
            calls+=1
            return self.policy if calls==1 else tuple({"revision":99} for _ in range(4))
        self.workflow._policy=policy
        def launch(command,**kwargs):
            return legacy.Process(returncode=None,poll=lambda:0 if killed else None,kill=lambda:killed.append(True),wait=lambda **kw:0)
        with patch("subprocess.Popen",side_effect=launch),self.assertRaises(HandoffConflict):
            self.run_worker()
        self.assertEqual(killed,[True])
        self.assertFalse(self.workflow.processes)

    def test_wrong_source_configuration_cannot_select_ko_child(self):
        self.policy[1]["symbol"]="F"
        with patch("subprocess.Popen",side_effect=AssertionError("No mismatched worker")),self.assertRaises(HandoffConflict):
            self.run_worker()
