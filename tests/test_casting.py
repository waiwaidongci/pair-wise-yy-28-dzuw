import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import ContinuityDB, DomainError

class CastingFlowTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=ContinuityDB(self.path)
        self.producer=self.db.add_user("制片","producer"); self.continuity=self.db.add_user("场记","continuity"); self.reviewer=self.db.add_user("审片","reviewer")
        self.production=self.db.create_production("替身风波","演员安排",self.producer)
        self.scene=self.db.add_scene(self.production,"S01","仓库对峙",1)
        self.s1=self.db.add_shot(self.scene,"S01-01",1,1,"开打",self.continuity,"action")
        self.s2=self.db.add_shot(self.scene,"S01-02",2,2,"对白",self.continuity)
        self.hero=self.db.add_element(self.production,"林岚","character","stable","主角")
        self.lead=self.db.add_actor(self.production,"沈亦","主演",self.producer)
        self.lead2=self.db.add_actor(self.production,"沈琪","二组主演",self.producer)
        self.double=self.db.add_actor(self.production,"阿杰","武替",self.producer)
        self.db.set_cast(self.hero,self.lead,"lead",self.producer)
        self.db.set_cast(self.hero,self.lead2,"lead",self.producer)
        self.db.set_cast(self.hero,self.double,"standin",self.producer)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def active(self): return self.db.list_conflicts(self.scene)
    def shot_status(self,shot_id):
        return [s for s in self.db.snapshot()["shots"] if s["id"]==shot_id][0]["status"]
    def test_standin_action_ok_dialogue_conflict(self):
        self.db.set_performance(self.s1,self.hero,self.double,self.continuity)
        self.assertEqual([],self.active())
        result=self.db.set_performance(self.s2,self.hero,self.double,self.continuity)
        self.assertEqual(["standin_in_dialogue"],[c["kind"] for c in result["conflicts"]])
    def test_lead_rotation_requires_reviewer_filing(self):
        self.db.set_performance(self.s1,self.hero,self.lead,self.continuity)
        self.db.set_performance(self.s2,self.hero,self.lead2,self.continuity)
        self.assertEqual(["lead_rotation"],[c["kind"] for c in self.active()])
        with self.assertRaisesRegex(DomainError,"审片"):
            self.db.file_rotation(self.hero,self.s2,self.lead2,"补拍备案",self.continuity)
        self.db.file_rotation(self.hero,self.s2,self.lead2,"二组接拍已备案",self.reviewer)
        self.assertEqual([],self.active())
        history=self.db.list_conflicts(self.scene,include_resolved=True)
        self.assertEqual(1,len(history)); self.assertEqual("resolved",history[0]["status"])
        self.assertEqual(1,len(self.db.snapshot()["rotation_filings"]))
    def test_unregistered_performer_then_register(self):
        outsider=self.db.add_actor(self.production,"路人","未登记",self.producer)
        self.db.set_performance(self.s2,self.hero,outsider,self.continuity)
        self.assertEqual(["unregistered_performer"],[c["kind"] for c in self.active()])
        self.db.set_cast(self.hero,outsider,"standin",self.producer)
        self.assertEqual(["standin_in_dialogue"],[c["kind"] for c in self.active()])
    def test_cast_change_reopens_locked_shot_and_keeps_history(self):
        self.db.set_performance(self.s2,self.hero,self.double,self.continuity)
        conflict=self.active()[0]
        self.db.approve_exemption(conflict["id"],"替身出镜为导演刻意安排",self.reviewer)
        self.db.lock_shot(self.s2,self.continuity)
        self.assertEqual("locked",self.shot_status(self.s2))
        self.db.set_performance(self.s2,self.hero,self.lead,self.continuity)
        self.assertEqual("planned",self.shot_status(self.s2))
        self.assertEqual([],self.active())
        history=self.db.list_conflicts(self.scene,include_resolved=True)
        self.assertEqual(1,len(history)); self.assertEqual(0,history[0]["active"])
        self.assertEqual(1,len(self.db.snapshot()["exemptions"]))
    def test_shot_type_and_cast_removal_recheck(self):
        self.db.set_performance(self.s1,self.hero,self.double,self.continuity)
        self.db.lock_shot(self.s1,self.continuity)
        self.db.set_shot_type(self.s1,"dialogue",self.continuity)
        self.assertEqual("planned",self.shot_status(self.s1))
        self.assertEqual(["standin_in_dialogue"],[c["kind"] for c in self.active()])
        self.db.remove_cast(self.hero,self.double,self.producer)
        self.assertEqual(["unregistered_performer"],[c["kind"] for c in self.active()])
    def test_validation(self):
        prop=self.db.add_element(self.production,"雨伞","prop","stable","")
        with self.assertRaisesRegex(DomainError,"角色元素"):
            self.db.set_cast(prop,self.lead,"lead",self.producer)
        with self.assertRaisesRegex(DomainError,"角色元素"):
            self.db.set_performance(self.s1,prop,self.lead,self.continuity)
        with self.assertRaisesRegex(DomainError,"lead 或 standin"):
            self.db.set_cast(self.hero,self.lead,"star",self.producer)
        with self.assertRaisesRegex(DomainError,"镜头类型"):
            self.db.set_shot_type(self.s1,"silent",self.continuity)
        with self.assertRaisesRegex(DomainError,"审片人员"):
            self.db.add_actor(self.production,"某演员","",self.reviewer)
        other=self.db.create_production("另一部","",self.producer)
        stranger=self.db.add_actor(other,"外人","",self.producer)
        with self.assertRaisesRegex(DomainError,"不属于该项目"):
            self.db.set_performance(self.s1,self.hero,stranger,self.continuity)

if __name__=="__main__": unittest.main()
