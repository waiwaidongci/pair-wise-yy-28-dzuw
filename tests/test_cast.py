import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import ContinuityDB, DomainError

class CastContinuityTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=ContinuityDB(self.path)
        self.producer=self.db.add_user("制片","producer"); self.continuity=self.db.add_user("场记","continuity"); self.reviewer=self.db.add_user("审片","reviewer")
        self.production=self.db.create_production("测试影片","演员连续性",self.producer)
        self.scene=self.db.add_scene(self.production,"S01","雨夜",1)
        self.s1=self.db.add_shot(self.scene,"S01-01",1,1,"对白特写",self.continuity)
        self.s2=self.db.add_shot(self.scene,"S01-02",2,2,"追车动作",self.continuity,shot_type="action")
        self.lead=self.db.add_actor(self.production,"林岚","主演")
        self.standin=self.db.add_actor(self.production,"赵武","替身")
        self.other=self.db.add_actor(self.production,"孙倩","轮换")
        self.role=self.db.add_role(self.production,"阿青",self.lead)
        self.db.add_standin(self.role,self.standin,self.continuity)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def test_standin_dialogue_conflicts_but_action_passes(self):
        self.db.set_shot_cast(self.s1,self.role,self.standin,self.continuity)
        self.db.set_shot_cast(self.s2,self.role,self.standin,self.continuity)
        conflicts=self.db.list_cast_conflicts(self.scene)
        self.assertEqual(1,len(conflicts))
        self.assertEqual("standin_in_dialogue",conflicts[0]["kind"])
        self.assertEqual(self.s1,conflicts[0]["shot_id"])
    def test_rotation_requires_reviewer_filing(self):
        self.db.set_shot_cast(self.s1,self.role,self.other,self.continuity)
        conflicts=self.db.list_cast_conflicts(self.scene)
        self.assertEqual("rotation_not_filed",conflicts[0]["kind"])
        with self.assertRaisesRegex(DomainError,"审片"):
            self.db.file_rotation(self.role,self.other,"档期调整轮换出演已确认",self.continuity)
        self.db.file_rotation(self.role,self.other,"档期调整轮换出演已确认",self.reviewer)
        self.assertEqual([],self.db.list_cast_conflicts(self.scene))
        history=self.db.list_cast_conflicts(self.scene,include_resolved=True)
        self.assertEqual(1,len(history)); self.assertEqual("resolved",history[0]["status"])
    def test_lead_change_reopens_locked_shots_and_flags_rotation(self):
        self.db.set_shot_cast(self.s1,self.role,self.lead,self.continuity)
        self.db.lock_shot(self.s1,self.continuity)
        result=self.db.change_lead(self.role,self.other,self.producer)
        self.assertIn(self.scene,result["rechecked_scenes"])
        shot=[s for s in self.db.snapshot()["shots"] if s["id"]==self.s1][0]
        self.assertEqual("planned",shot["status"])
        conflicts=self.db.list_cast_conflicts(self.scene)
        self.assertEqual("rotation_not_filed",conflicts[0]["kind"])
        self.db.file_rotation(self.role,self.lead,"原主演回归戏份已备案",self.reviewer)
        self.assertEqual([],self.db.list_cast_conflicts(self.scene))
        history=self.db.list_cast_conflicts(self.scene,include_resolved=True)
        self.assertEqual("resolved",history[0]["status"])
    def test_cast_conflict_blocks_lock_and_exemption_allows(self):
        self.db.set_shot_cast(self.s1,self.role,self.standin,self.continuity)
        with self.assertRaisesRegex(DomainError,"未处理冲突"):
            self.db.lock_shot(self.s1,self.continuity)
        conflict=self.db.list_cast_conflicts(self.scene)[0]
        self.db.exempt_cast_conflict(conflict["id"],"替身出演已获导演组书面确认",self.reviewer)
        self.db.lock_shot(self.s1,self.continuity)
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.set_shot_cast(self.s1,self.role,self.lead,self.continuity)
    def test_standin_removal_and_type_change_recheck(self):
        self.db.set_shot_cast(self.s2,self.role,self.standin,self.continuity)
        self.assertEqual([],self.db.list_cast_conflicts(self.scene))
        self.db.set_shot_type(self.s2,"dialogue",self.continuity)
        self.assertEqual("standin_in_dialogue",self.db.list_cast_conflicts(self.scene)[0]["kind"])
        self.db.set_shot_type(self.s2,"action",self.continuity)
        self.assertEqual([],self.db.list_cast_conflicts(self.scene))
        self.db.remove_standin(self.role,self.standin,self.continuity)
        self.assertEqual("rotation_not_filed",self.db.list_cast_conflicts(self.scene)[0]["kind"])
    def test_cast_validation(self):
        with self.assertRaisesRegex(DomainError,"主演不能同时"):
            self.db.add_standin(self.role,self.lead,self.continuity)
        with self.assertRaisesRegex(DomainError,"替身未登记"):
            self.db.remove_standin(self.role,self.other,self.continuity)
        with self.assertRaisesRegex(DomainError,"无需轮换备案"):
            self.db.file_rotation(self.role,self.lead,"主演本人不需要备案",self.reviewer)
        with self.assertRaisesRegex(DomainError,"镜头类型"):
            self.db.set_shot_type(self.s1,"silent",self.continuity)

if __name__=="__main__": unittest.main()
