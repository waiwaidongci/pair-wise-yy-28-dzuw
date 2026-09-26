from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class DomainError(ValueError):
    """Business rule violation."""


ELEMENT_KINDS = {"character", "costume", "prop", "injury"}
RULES = {"stable", "monotonic", "allowed"}
CAST_ROLES = {"lead", "standin"}
SHOT_TYPES = {"dialogue", "action"}


class ContinuityDB:
    """Non-linear film continuity checker with reviewable corrections."""

    def __init__(self, path: str = "continuity.db") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._schema()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              role TEXT NOT NULL CHECK(role IN ('producer','continuity','reviewer'))
            );
            CREATE TABLE IF NOT EXISTS productions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              created_by INTEGER NOT NULL REFERENCES users(id),
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
              scene_number TEXT NOT NULL,
              title TEXT NOT NULL,
              narrative_order INTEGER NOT NULL CHECK(narrative_order > 0),
              UNIQUE(production_id,scene_number),
              UNIQUE(production_id,narrative_order)
            );
            CREATE TABLE IF NOT EXISTS shots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
              shot_code TEXT NOT NULL,
              shoot_order INTEGER NOT NULL CHECK(shoot_order > 0),
              narrative_order INTEGER NOT NULL CHECK(narrative_order > 0),
              shot_type TEXT NOT NULL DEFAULT 'dialogue' CHECK(shot_type IN ('dialogue','action')),
              description TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','locked')),
              version INTEGER NOT NULL DEFAULT 0,
              updated_by INTEGER NOT NULL REFERENCES users(id),
              updated_at TEXT NOT NULL,
              UNIQUE(scene_id,shot_code),
              UNIQUE(scene_id,narrative_order)
            );
            CREATE TABLE IF NOT EXISTS elements (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('character','costume','prop','injury')),
              rule TEXT NOT NULL CHECK(rule IN ('stable','monotonic','allowed')),
              description TEXT NOT NULL DEFAULT '',
              UNIQUE(production_id,name)
            );
            CREATE TABLE IF NOT EXISTS element_transitions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              element_id INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
              from_state TEXT NOT NULL,
              to_state TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              UNIQUE(element_id,from_state,to_state)
            );
            CREATE TABLE IF NOT EXISTS element_states (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              shot_id INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
              element_id INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
              state_value TEXT NOT NULL,
              numeric_value REAL,
              note TEXT NOT NULL DEFAULT '',
              updated_by INTEGER NOT NULL REFERENCES users(id),
              updated_at TEXT NOT NULL,
              UNIQUE(shot_id,element_id)
            );
            CREATE TABLE IF NOT EXISTS conflicts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
              element_id INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
              from_shot_id INTEGER NOT NULL REFERENCES shots(id),
              to_shot_id INTEGER NOT NULL REFERENCES shots(id),
              kind TEXT NOT NULL,
              detail TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','exempted','resolved')),
              active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
              fingerprint TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS adjustment_plans (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conflict_id INTEGER NOT NULL UNIQUE REFERENCES conflicts(id),
              shot_id INTEGER NOT NULL REFERENCES shots(id),
              element_id INTEGER NOT NULL REFERENCES elements(id),
              new_value TEXT NOT NULL,
              numeric_value REAL,
              reason TEXT NOT NULL,
              proposed_by INTEGER NOT NULL REFERENCES users(id),
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
              reviewed_by INTEGER REFERENCES users(id),
              review_note TEXT NOT NULL DEFAULT '',
              proposed_at TEXT NOT NULL,
              reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS exemptions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conflict_id INTEGER NOT NULL UNIQUE REFERENCES conflicts(id),
              reason TEXT NOT NULL,
              approved_by INTEGER NOT NULL REFERENCES users(id),
              approved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actors (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(production_id,name)
            );
            CREATE TABLE IF NOT EXISTS character_cast (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              element_id INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
              actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
              cast_role TEXT NOT NULL CHECK(cast_role IN ('lead','standin')),
              updated_by INTEGER NOT NULL REFERENCES users(id),
              updated_at TEXT NOT NULL,
              UNIQUE(element_id,actor_id)
            );
            CREATE TABLE IF NOT EXISTS shot_performances (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              shot_id INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
              element_id INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
              actor_id INTEGER NOT NULL REFERENCES actors(id),
              updated_by INTEGER NOT NULL REFERENCES users(id),
              updated_at TEXT NOT NULL,
              UNIQUE(shot_id,element_id)
            );
            CREATE TABLE IF NOT EXISTS rotation_filings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              element_id INTEGER NOT NULL REFERENCES elements(id) ON DELETE CASCADE,
              shot_id INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
              actor_id INTEGER NOT NULL REFERENCES actors(id),
              note TEXT NOT NULL DEFAULT '',
              filed_by INTEGER NOT NULL REFERENCES users(id),
              filed_at TEXT NOT NULL,
              UNIQUE(element_id,shot_id,actor_id)
            );
            """
        )
        shot_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(shots)")}
        if "shot_type" not in shot_cols:
            self.conn.execute(
                "ALTER TABLE shots ADD COLUMN shot_type TEXT NOT NULL DEFAULT 'dialogue' "
                "CHECK(shot_type IN ('dialogue','action'))"
            )
        self.conn.commit()

    def seed_demo(self) -> None:
        if self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        producer = self.add_user("制片", "producer")
        continuity = self.add_user("场记", "continuity")
        reviewer = self.add_user("审片", "reviewer")
        production = self.create_production("雨夜追踪", "非线性拍摄出的连续性示例", producer)
        scene = self.add_scene(production, "S01", "巷口相遇", 1)
        s01 = self.add_shot(scene, "S01-01", 2, 1, "角色受伤后", continuity)
        s02 = self.add_shot(scene, "S01-02", 1, 2, "角色尚未受伤", continuity)
        injury = self.add_element(production, "主角左臂伤痕", "injury", "monotonic", "伤痕严重程度只能递增")
        self.set_element_state(s01, injury, "重度", 3, "", continuity)
        self.set_element_state(s02, injury, "轻度", 1, "", continuity)
        lead = self.add_actor(production, "沈亦", "主演", producer)
        double = self.add_actor(production, "阿杰", "动作替身", producer)
        hero = self.add_element(production, "林岚（主角）", "character", "stable", "主角，动作戏可用替身")
        self.set_cast(hero, lead, "lead", producer)
        self.set_cast(hero, double, "standin", producer)
        self.set_shot_type(s01, "action", continuity)
        self.set_performance(s01, hero, double, continuity)
        self.set_performance(s02, hero, double, continuity)
        self.check_scene(scene)

    def add_user(self, name: str, role: str) -> int:
        if not name.strip() or role not in {"producer", "continuity", "reviewer"}:
            raise DomainError("用户名或角色无效")
        with self.transaction():
            try:
                cur = self.conn.execute("INSERT INTO users(name,role) VALUES(?,?)", (name.strip(), role))
            except sqlite3.IntegrityError as exc:
                raise DomainError("用户名已存在") from exc
        return int(cur.lastrowid)

    def create_production(self, title: str, description: str, user_id: int) -> int:
        user = self.conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["role"] != "producer" or not title.strip():
            raise DomainError("只有制片人可以创建项目")
        with self.transaction():
            cur = self.conn.execute(
                "INSERT INTO productions(title,description,created_by,created_at) VALUES(?,?,?,?)",
                (title.strip(), description.strip(), user_id, datetime.now().isoformat()),
            )
        return int(cur.lastrowid)

    def _production_for_user(self, production_id: int, user_id: int) -> sqlite3.Row:
        user = self.conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise DomainError("用户不存在")
        if user["role"] == "reviewer":
            raise DomainError("审片人员只能审核方案和豁免，不能直接编排")
        return user

    def add_scene(self, production_id: int, scene_number: str, title: str, narrative_order: int) -> int:
        if not self.conn.execute("SELECT 1 FROM productions WHERE id=?", (production_id,)).fetchone():
            raise DomainError("项目不存在")
        if not scene_number.strip() or not title.strip() or narrative_order <= 0:
            raise DomainError("场次参数无效")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO scenes(production_id,scene_number,title,narrative_order) VALUES(?,?,?,?)",
                    (production_id, scene_number.strip(), title.strip(), narrative_order),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("场次编号或叙事顺序重复") from exc
        return int(cur.lastrowid)

    def add_shot(self, scene_id: int, shot_code: str, shoot_order: int, narrative_order: int,
                 description: str, user_id: int, shot_type: str = "dialogue") -> int:
        scene = self.conn.execute("SELECT production_id FROM scenes WHERE id=?", (scene_id,)).fetchone()
        if not scene:
            raise DomainError("场次不存在")
        user = self._production_for_user(scene["production_id"], user_id)
        if user["role"] not in {"producer", "continuity"}:
            raise DomainError("无权创建镜头")
        if not shot_code.strip() or shoot_order <= 0 or narrative_order <= 0:
            raise DomainError("镜头参数无效")
        if shot_type not in SHOT_TYPES:
            raise DomainError("镜头类型必须是 dialogue 或 action")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO shots(scene_id,shot_code,shoot_order,narrative_order,shot_type,description,updated_by,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (scene_id, shot_code.strip(), shoot_order, narrative_order, shot_type, description.strip(), user_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("场次内镜头编号或叙事顺序重复") from exc
        return int(cur.lastrowid)

    def add_element(self, production_id: int, name: str, kind: str, rule: str, description: str = "") -> int:
        if not self.conn.execute("SELECT 1 FROM productions WHERE id=?", (production_id,)).fetchone():
            raise DomainError("项目不存在")
        if not name.strip() or kind not in ELEMENT_KINDS or rule not in RULES:
            raise DomainError("连续性元素参数无效")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO elements(production_id,name,kind,rule,description) VALUES(?,?,?,?,?)",
                    (production_id, name.strip(), kind, rule, description.strip()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("项目内元素名称不能重复") from exc
        return int(cur.lastrowid)

    def add_transition(self, element_id: int, from_state: str, to_state: str, note: str = "") -> int:
        element = self.conn.execute("SELECT rule FROM elements WHERE id=?", (element_id,)).fetchone()
        if not element or element["rule"] != "allowed":
            raise DomainError("只有 allowed 规则元素需要配置状态转移")
        if not from_state.strip() or not to_state.strip() or from_state == to_state:
            raise DomainError("状态转移必须包含两个不同状态")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO element_transitions(element_id,from_state,to_state,note) VALUES(?,?,?,?)",
                    (element_id, from_state.strip(), to_state.strip(), note.strip()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("该状态转移已存在") from exc
        return int(cur.lastrowid)

    def _character_element(self, element_id: int) -> sqlite3.Row:
        element = self.conn.execute("SELECT * FROM elements WHERE id=?", (element_id,)).fetchone()
        if not element or element["kind"] != "character":
            raise DomainError("只有角色元素可以安排演员")
        return element

    def _actor_in_production(self, production_id: int, actor_id: int) -> sqlite3.Row:
        actor = self.conn.execute("SELECT * FROM actors WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["production_id"] != production_id:
            raise DomainError("演员不属于该项目")
        return actor

    def add_actor(self, production_id: int, name: str, note: str, user_id: int) -> int:
        if not self.conn.execute("SELECT 1 FROM productions WHERE id=?", (production_id,)).fetchone():
            raise DomainError("项目不存在")
        self._production_for_user(production_id, user_id)
        if not name.strip():
            raise DomainError("演员姓名不能为空")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO actors(production_id,name,note,created_at) VALUES(?,?,?,?)",
                    (production_id, name.strip(), note.strip(), datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("该项目已存在同名演员") from exc
        return int(cur.lastrowid)

    def set_cast(self, element_id: int, actor_id: int, cast_role: str, user_id: int) -> dict:
        element = self._character_element(element_id)
        self._production_for_user(element["production_id"], user_id)
        self._actor_in_production(element["production_id"], actor_id)
        if cast_role not in CAST_ROLES:
            raise DomainError("选角身份必须是 lead 或 standin")
        with self.transaction():
            try:
                self.conn.execute(
                    "INSERT INTO character_cast(element_id,actor_id,cast_role,updated_by,updated_at) VALUES(?,?,?,?,?)",
                    (element_id, actor_id, cast_role, user_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "UPDATE character_cast SET cast_role=?,updated_by=?,updated_at=? WHERE element_id=? AND actor_id=?",
                    (cast_role, user_id, datetime.now().isoformat(), element_id, actor_id),
                )
            reopened, scenes = self._recheck_cast(element_id, user_id)
        return {"element_id": element_id, "actor_id": actor_id, "cast_role": cast_role,
                "reopened_shots": reopened, "scenes": scenes}

    def remove_cast(self, element_id: int, actor_id: int, user_id: int) -> dict:
        element = self._character_element(element_id)
        self._production_for_user(element["production_id"], user_id)
        with self.transaction():
            cur = self.conn.execute(
                "DELETE FROM character_cast WHERE element_id=? AND actor_id=?", (element_id, actor_id)
            )
            if cur.rowcount == 0:
                raise DomainError("该演员未登记在此角色下")
            reopened, scenes = self._recheck_cast(element_id, user_id)
        return {"element_id": element_id, "actor_id": actor_id,
                "reopened_shots": reopened, "scenes": scenes}

    def set_element_state(self, shot_id: int, element_id: int, state_value: str, numeric_value: float | None,
                          note: str, user_id: int) -> dict:
        shot = self.conn.execute("SELECT s.*,sc.production_id FROM shots s JOIN scenes sc ON sc.id=s.scene_id WHERE s.id=?", (shot_id,)).fetchone()
        element = self.conn.execute("SELECT * FROM elements WHERE id=?", (element_id,)).fetchone()
        if not shot or not element or shot["production_id"] != element["production_id"]:
            raise DomainError("镜头与元素不属于同一项目")
        user = self._production_for_user(shot["production_id"], user_id)
        if user["role"] not in {"producer", "continuity"}:
            raise DomainError("无权修改连续性状态")
        if shot["status"] == "locked":
            raise DomainError("镜头已锁定，不能直接修改状态")
        if not state_value.strip():
            raise DomainError("状态值不能为空")
        if element["rule"] == "monotonic" and numeric_value is None:
            raise DomainError("单调规则必须提供 numeric_value")
        with self.transaction():
            try:
                self.conn.execute(
                    "INSERT INTO element_states(shot_id,element_id,state_value,numeric_value,note,updated_by,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (shot_id, element_id, state_value.strip(), numeric_value, note.strip(), user_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "UPDATE element_states SET state_value=?,numeric_value=?,note=?,updated_by=?,updated_at=? WHERE shot_id=? AND element_id=?",
                    (state_value.strip(), numeric_value, note.strip(), user_id, datetime.now().isoformat(), shot_id, element_id),
                )
            self.conn.execute("UPDATE shots SET version=version+1,updated_by=?,updated_at=? WHERE id=?", (user_id, datetime.now().isoformat(), shot_id))
            self._sync_conflicts(shot["scene_id"])
        return {"shot_id": shot_id, "element_id": element_id, "conflicts": self.list_conflicts(shot["scene_id"])}

    def _reopen_and_recheck(self, shot_ids: list[int], user_id: int) -> tuple[list[int], list[int]]:
        """演员安排变更后，涉及镜头回到待处理并重新检查所在场次。"""
        now = datetime.now().isoformat()
        reopened: list[int] = []
        scene_ids: set[int] = set()
        for shot_id in dict.fromkeys(shot_ids):
            shot = self.conn.execute("SELECT scene_id,status FROM shots WHERE id=?", (shot_id,)).fetchone()
            if not shot:
                continue
            scene_ids.add(shot["scene_id"])
            if shot["status"] == "locked":
                self.conn.execute(
                    "UPDATE shots SET status='planned',version=version+1,updated_by=?,updated_at=? WHERE id=?",
                    (user_id, now, shot_id),
                )
                reopened.append(shot_id)
        for scene_id in sorted(scene_ids):
            self._sync_conflicts(scene_id)
        return reopened, sorted(scene_ids)

    def _recheck_cast(self, element_id: int, user_id: int) -> tuple[list[int], list[int]]:
        shot_ids = [r[0] for r in self.conn.execute(
            "SELECT shot_id FROM shot_performances WHERE element_id=?", (element_id,)
        )]
        return self._reopen_and_recheck(shot_ids, user_id)

    def set_performance(self, shot_id: int, element_id: int, actor_id: int | None, user_id: int) -> dict:
        shot = self.conn.execute("SELECT s.*,sc.production_id FROM shots s JOIN scenes sc ON sc.id=s.scene_id WHERE s.id=?", (shot_id,)).fetchone()
        if not shot:
            raise DomainError("镜头不存在")
        element = self._character_element(element_id)
        if element["production_id"] != shot["production_id"]:
            raise DomainError("镜头与角色不属于同一项目")
        self._production_for_user(shot["production_id"], user_id)
        with self.transaction():
            if actor_id:
                self._actor_in_production(shot["production_id"], actor_id)
                try:
                    self.conn.execute(
                        "INSERT INTO shot_performances(shot_id,element_id,actor_id,updated_by,updated_at) VALUES(?,?,?,?,?)",
                        (shot_id, element_id, actor_id, user_id, datetime.now().isoformat()),
                    )
                except sqlite3.IntegrityError:
                    self.conn.execute(
                        "UPDATE shot_performances SET actor_id=?,updated_by=?,updated_at=? WHERE shot_id=? AND element_id=?",
                        (actor_id, user_id, datetime.now().isoformat(), shot_id, element_id),
                    )
            else:
                cur = self.conn.execute(
                    "DELETE FROM shot_performances WHERE shot_id=? AND element_id=?", (shot_id, element_id)
                )
                if cur.rowcount == 0:
                    raise DomainError("该镜头未登记此角色的出演者")
            reopened, _ = self._reopen_and_recheck([shot_id], user_id)
        return {"shot_id": shot_id, "element_id": element_id, "actor_id": actor_id or None,
                "reopened_shots": reopened, "conflicts": self.list_conflicts(shot["scene_id"])}

    def set_shot_type(self, shot_id: int, shot_type: str, user_id: int) -> dict:
        shot = self.conn.execute("SELECT s.*,sc.production_id FROM shots s JOIN scenes sc ON sc.id=s.scene_id WHERE s.id=?", (shot_id,)).fetchone()
        if not shot:
            raise DomainError("镜头不存在")
        self._production_for_user(shot["production_id"], user_id)
        if shot_type not in SHOT_TYPES:
            raise DomainError("镜头类型必须是 dialogue 或 action")
        with self.transaction():
            self.conn.execute(
                "UPDATE shots SET shot_type=?,version=version+1,updated_by=?,updated_at=? WHERE id=?",
                (shot_type, user_id, datetime.now().isoformat(), shot_id),
            )
            reopened, _ = self._reopen_and_recheck([shot_id], user_id)
        return {"shot_id": shot_id, "shot_type": shot_type,
                "reopened_shots": reopened, "conflicts": self.list_conflicts(shot["scene_id"])}

    def file_rotation(self, element_id: int, shot_id: int, actor_id: int, note: str, reviewer_id: int) -> int:
        reviewer = self.conn.execute("SELECT role FROM users WHERE id=?", (reviewer_id,)).fetchone()
        if not reviewer or reviewer["role"] != "reviewer":
            raise DomainError("只有审片人可以备案主演轮换")
        element = self._character_element(element_id)
        shot = self.conn.execute("SELECT s.*,sc.production_id FROM shots s JOIN scenes sc ON sc.id=s.scene_id WHERE s.id=?", (shot_id,)).fetchone()
        if not shot or shot["production_id"] != element["production_id"]:
            raise DomainError("镜头与角色不属于同一项目")
        cast = self.conn.execute(
            "SELECT cast_role FROM character_cast WHERE element_id=? AND actor_id=?", (element_id, actor_id)
        ).fetchone()
        if not cast or cast["cast_role"] != "lead":
            raise DomainError("备案演员必须是该角色登记的主演")
        if not note.strip():
            raise DomainError("备案说明不能为空")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO rotation_filings(element_id,shot_id,actor_id,note,filed_by,filed_at) VALUES(?,?,?,?,?,?)",
                    (element_id, shot_id, actor_id, note.strip(), reviewer_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("该镜头的主演轮换已备案") from exc
            self._sync_conflicts(shot["scene_id"])
        return int(cur.lastrowid)

    def _detect_conflicts(self, scene_id: int) -> list[dict]:
        scene = self.conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
        if not scene:
            raise DomainError("场次不存在")
        shots = self.conn.execute(
            "SELECT * FROM shots WHERE scene_id=? ORDER BY narrative_order", (scene_id,)
        ).fetchall()
        elements = self.conn.execute("SELECT * FROM elements WHERE production_id=? ORDER BY id", (scene["production_id"],)).fetchall()
        detected: list[dict] = []
        for element in elements:
            sequence = []
            for shot in shots:
                state = self.conn.execute(
                    "SELECT * FROM element_states WHERE shot_id=? AND element_id=?", (shot["id"], element["id"])
                ).fetchone()
                if state:
                    sequence.append((shot, state))
            for (prev_shot, prev), (shot, current) in zip(sequence, sequence[1:]):
                kind = None
                detail = ""
                if element["rule"] == "stable":
                    if current["state_value"] != prev["state_value"]:
                        kind = "state_changed"
                        detail = f"{element['name']} 应为稳定状态，却从 {prev['state_value']} 变为 {current['state_value']}"
                elif element["rule"] == "monotonic":
                    if current["numeric_value"] is None or prev["numeric_value"] is None:
                        kind = "missing_numeric_value"
                        detail = f"{element['name']} 缺少可比较的数值"
                    elif current["numeric_value"] < prev["numeric_value"]:
                        kind = "regression"
                        detail = f"{element['name']} 在叙事顺序中从 {prev['numeric_value']} 回退到 {current['numeric_value']}"
                else:
                    allowed = self.conn.execute(
                        "SELECT 1 FROM element_transitions WHERE element_id=? AND from_state=? AND to_state=?",
                        (element["id"], prev["state_value"], current["state_value"]),
                    ).fetchone()
                    if not allowed:
                        kind = "transition_not_allowed"
                        detail = f"{element['name']} 不允许从 {prev['state_value']} 变为 {current['state_value']}"
                if kind:
                    fingerprint = f"{scene_id}:{element['id']}:{prev_shot['id']}:{shot['id']}:{kind}"
                    detected.append({
                        "scene_id": scene_id, "element_id": element["id"], "element_name": element["name"],
                        "from_shot_id": prev_shot["id"], "to_shot_id": shot["id"], "kind": kind,
                        "detail": detail, "fingerprint": fingerprint,
                    })
        return detected

    def _detect_cast_conflicts(self, scene_id: int) -> list[dict]:
        scene = self.conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
        shots = self.conn.execute(
            "SELECT * FROM shots WHERE scene_id=? ORDER BY narrative_order", (scene_id,)
        ).fetchall()
        characters = self.conn.execute(
            "SELECT * FROM elements WHERE production_id=? AND kind='character' ORDER BY id", (scene["production_id"],)
        ).fetchall()
        actor_names = {r["id"]: r["name"] for r in self.conn.execute(
            "SELECT id,name FROM actors WHERE production_id=?", (scene["production_id"],)
        )}
        detected: list[dict] = []
        for character in characters:
            cast = {r["actor_id"]: r["cast_role"] for r in self.conn.execute(
                "SELECT actor_id,cast_role FROM character_cast WHERE element_id=?", (character["id"],)
            )}
            filings = {(r["shot_id"], r["actor_id"]) for r in self.conn.execute(
                "SELECT shot_id,actor_id FROM rotation_filings WHERE element_id=?", (character["id"],)
            )}
            prev = None
            for shot in shots:
                perf = self.conn.execute(
                    "SELECT actor_id FROM shot_performances WHERE shot_id=? AND element_id=?",
                    (shot["id"], character["id"]),
                ).fetchone()
                if not perf:
                    continue
                actor_id = perf["actor_id"]
                role = cast.get(actor_id)
                name = actor_names.get(actor_id, str(actor_id))
                kind = None
                detail = ""
                from_shot = shot
                if role is None:
                    kind = "unregistered_performer"
                    detail = f"{name} 未登记为 {character['name']} 的主演或替身，却出演镜头 {shot['shot_code']}"
                elif role == "standin" and shot["shot_type"] != "action":
                    kind = "standin_in_dialogue"
                    detail = f"对白镜头 {shot['shot_code']} 中 {character['name']} 由替身 {name} 出演"
                elif (role == "lead" and prev and prev["role"] == "lead" and prev["actor_id"] != actor_id
                      and (shot["id"], actor_id) not in filings):
                    kind = "lead_rotation"
                    detail = f"{character['name']} 的主演从 {prev['name']} 轮换为 {name}（镜头 {shot['shot_code']}），未经审片备案"
                    from_shot = prev["shot"]
                if kind:
                    fingerprint = f"{scene_id}:{character['id']}:{from_shot['id']}:{shot['id']}:{kind}"
                    detected.append({
                        "scene_id": scene_id, "element_id": character["id"], "element_name": character["name"],
                        "from_shot_id": from_shot["id"], "to_shot_id": shot["id"], "kind": kind,
                        "detail": detail, "fingerprint": fingerprint,
                    })
                prev = {"shot": shot, "actor_id": actor_id, "role": role, "name": name}
        return detected

    def _sync_conflicts(self, scene_id: int) -> None:
        detected = self._detect_conflicts(scene_id) + self._detect_cast_conflicts(scene_id)
        active_fingerprints = {row["fingerprint"] for row in detected}
        for row in self.conn.execute("SELECT * FROM conflicts WHERE scene_id=? AND active=1", (scene_id,)).fetchall():
            if row["fingerprint"] not in active_fingerprints:
                self.conn.execute(
                    "UPDATE conflicts SET active=0,status='resolved',updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), row["id"]),
                )
        for issue in detected:
            existing = self.conn.execute("SELECT * FROM conflicts WHERE fingerprint=?", (issue["fingerprint"],)).fetchone()
            if existing:
                status = "exempted" if existing["status"] == "exempted" else "open"
                self.conn.execute(
                    "UPDATE conflicts SET active=1,status=?,detail=?,updated_at=? WHERE id=?",
                    (status, issue["detail"], datetime.now().isoformat(), existing["id"]),
                )
            else:
                self.conn.execute(
                    "INSERT INTO conflicts(scene_id,element_id,from_shot_id,to_shot_id,kind,detail,status,active,fingerprint,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?, 'open',1,?,?,?)",
                    (issue["scene_id"], issue["element_id"], issue["from_shot_id"], issue["to_shot_id"], issue["kind"], issue["detail"], issue["fingerprint"], datetime.now().isoformat(), datetime.now().isoformat()),
                )

    def check_scene(self, scene_id: int) -> list[dict]:
        if not self.conn.execute("SELECT 1 FROM scenes WHERE id=?", (scene_id,)).fetchone():
            raise DomainError("场次不存在")
        with self.transaction():
            self._sync_conflicts(scene_id)
        return self.list_conflicts(scene_id)

    def list_conflicts(self, scene_id: int, include_resolved: bool = False) -> list[dict]:
        clause = "" if include_resolved else "AND c.active=1"
        return [dict(row) for row in self.conn.execute(
            "SELECT c.*,e.name AS element_name,fs.shot_code AS from_shot_code,ts.shot_code AS to_shot_code "
            "FROM conflicts c JOIN elements e ON e.id=c.element_id JOIN shots fs ON fs.id=c.from_shot_id JOIN shots ts ON ts.id=c.to_shot_id "
            f"WHERE c.scene_id=? {clause} ORDER BY c.id", (scene_id,)
        ).fetchall()]

    def propose_adjustment(self, conflict_id: int, new_value: str, numeric_value: float | None,
                           reason: str, user_id: int) -> int:
        conflict = self.conn.execute("SELECT * FROM conflicts WHERE id=?", (conflict_id,)).fetchone()
        if not conflict or not conflict["active"]:
            raise DomainError("冲突不存在或已解决")
        if conflict["status"] != "open":
            raise DomainError("已豁免冲突不能提交状态调整方案")
        shot = self.conn.execute("SELECT * FROM shots WHERE id=?", (conflict["to_shot_id"],)).fetchone()
        element = self.conn.execute("SELECT * FROM elements WHERE id=?", (conflict["element_id"],)).fetchone()
        user = self._production_for_user(element["production_id"], user_id)
        if user["role"] not in {"producer", "continuity"}:
            raise DomainError("无权提出调整方案")
        if shot["status"] == "locked":
            raise DomainError("目标镜头已锁定")
        if not new_value.strip() or len(reason.strip()) < 3:
            raise DomainError("新状态和调整理由必须填写")
        if element["rule"] == "monotonic" and numeric_value is None:
            raise DomainError("单调规则调整必须提供 numeric_value")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO adjustment_plans(conflict_id,shot_id,element_id,new_value,numeric_value,reason,proposed_by,proposed_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (conflict_id, shot["id"], element["id"], new_value.strip(), numeric_value, reason.strip(), user_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("该冲突已有调整方案") from exc
        return int(cur.lastrowid)

    def review_adjustment(self, plan_id: int, approve: bool, reviewer_id: int, note: str = "") -> dict:
        reviewer = self.conn.execute("SELECT role FROM users WHERE id=?", (reviewer_id,)).fetchone()
        if not reviewer or reviewer["role"] != "reviewer":
            raise DomainError("只有审片人可以审核调整方案")
        plan = self.conn.execute("SELECT * FROM adjustment_plans WHERE id=?", (plan_id,)).fetchone()
        if not plan or plan["status"] != "pending":
            raise DomainError("调整方案不存在或已审核")
        if plan["proposed_by"] == reviewer_id:
            raise DomainError("提案人不能审核自己的方案")
        shot = self.conn.execute("SELECT * FROM shots WHERE id=?", (plan["shot_id"],)).fetchone()
        if shot["status"] == "locked":
            raise DomainError("目标镜头已锁定")
        with self.transaction():
            status = "approved" if approve else "rejected"
            self.conn.execute(
                "UPDATE adjustment_plans SET status=?,reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
                (status, reviewer_id, note.strip(), datetime.now().isoformat(), plan_id),
            )
            if approve:
                try:
                    self.conn.execute(
                        "INSERT INTO element_states(shot_id,element_id,state_value,numeric_value,note,updated_by,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (plan["shot_id"], plan["element_id"], plan["new_value"], plan["numeric_value"], f"调整方案 #{plan_id}", reviewer_id, datetime.now().isoformat()),
                    )
                except sqlite3.IntegrityError:
                    self.conn.execute(
                        "UPDATE element_states SET state_value=?,numeric_value=?,note=?,updated_by=?,updated_at=? WHERE shot_id=? AND element_id=?",
                        (plan["new_value"], plan["numeric_value"], f"调整方案 #{plan_id}", reviewer_id, datetime.now().isoformat(), plan["shot_id"], plan["element_id"]),
                    )
                self.conn.execute(
                    "UPDATE conflicts SET active=0,status='resolved',updated_at=? WHERE id=?",
                    (datetime.now().isoformat(), plan["conflict_id"]),
                )
                self._sync_conflicts(shot["scene_id"])
        return {"plan_id": plan_id, "status": status, "conflicts": self.list_conflicts(shot["scene_id"])}

    def approve_exemption(self, conflict_id: int, reason: str, reviewer_id: int) -> int:
        reviewer = self.conn.execute("SELECT role FROM users WHERE id=?", (reviewer_id,)).fetchone()
        conflict = self.conn.execute("SELECT * FROM conflicts WHERE id=?", (conflict_id,)).fetchone()
        if not conflict or not conflict["active"] or not reviewer or reviewer["role"] != "reviewer":
            raise DomainError("冲突或审片人无效")
        if len(reason.strip()) < 8:
            raise DomainError("豁免理由至少8个字符")
        with self.transaction():
            try:
                cur = self.conn.execute(
                    "INSERT INTO exemptions(conflict_id,reason,approved_by,approved_at) VALUES(?,?,?,?)",
                    (conflict_id, reason.strip(), reviewer_id, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("该冲突已经豁免") from exc
            self.conn.execute("UPDATE conflicts SET status='exempted',updated_at=? WHERE id=?", (datetime.now().isoformat(), conflict_id))
        return int(cur.lastrowid)

    def lock_shot(self, shot_id: int, user_id: int) -> None:
        shot = self.conn.execute("SELECT s.*,sc.production_id FROM shots s JOIN scenes sc ON sc.id=s.scene_id WHERE s.id=?", (shot_id,)).fetchone()
        if not shot:
            raise DomainError("镜头不存在")
        user = self._production_for_user(shot["production_id"], user_id)
        if user["role"] not in {"producer", "continuity"}:
            raise DomainError("无权锁定镜头")
        with self.transaction():
            self._sync_conflicts(shot["scene_id"])
            blocking = self.conn.execute(
                "SELECT COUNT(*) FROM conflicts WHERE scene_id=? AND active=1 AND status!='exempted'", (shot["scene_id"],)
            ).fetchone()[0]
            if blocking:
                raise DomainError(f"场次仍有 {blocking} 个未处理冲突，不能锁定")
            self.conn.execute("UPDATE shots SET status='locked',version=version+1,updated_by=?,updated_at=? WHERE id=?", (user_id, datetime.now().isoformat(), shot_id))

    def continuity_report(self, production_id: int) -> dict:
        production = self.conn.execute("SELECT * FROM productions WHERE id=?", (production_id,)).fetchone()
        if not production:
            raise DomainError("项目不存在")
        scenes = []
        for scene in self.conn.execute("SELECT * FROM scenes WHERE production_id=? ORDER BY narrative_order", (production_id,)).fetchall():
            shots = [dict(r) for r in self.conn.execute("SELECT * FROM shots WHERE scene_id=? ORDER BY narrative_order", (scene["id"],))]
            for shot in shots:
                shot["performances"] = [dict(p) for p in self.conn.execute(
                    "SELECT sp.element_id,e.name AS element_name,sp.actor_id,a.name AS actor_name "
                    "FROM shot_performances sp JOIN elements e ON e.id=sp.element_id JOIN actors a ON a.id=sp.actor_id "
                    "WHERE sp.shot_id=? ORDER BY sp.id", (shot["id"],))]
            conflicts = self.list_conflicts(scene["id"], include_resolved=True)
            scenes.append({**dict(scene), "shots": shots, "conflicts": conflicts})
        return {
            "production": dict(production),
            "elements": [dict(r) for r in self.conn.execute("SELECT * FROM elements WHERE production_id=? ORDER BY id", (production_id,))],
            "actors": [dict(r) for r in self.conn.execute("SELECT * FROM actors WHERE production_id=? ORDER BY id", (production_id,))],
            "cast": [dict(r) for r in self.conn.execute(
                "SELECT cc.element_id,e.name AS element_name,cc.actor_id,a.name AS actor_name,cc.cast_role "
                "FROM character_cast cc JOIN elements e ON e.id=cc.element_id JOIN actors a ON a.id=cc.actor_id "
                "WHERE e.production_id=? ORDER BY cc.id", (production_id,))],
            "filings": [dict(r) for r in self.conn.execute(
                "SELECT rf.element_id,e.name AS element_name,rf.shot_id,s.shot_code,rf.actor_id,a.name AS actor_name,rf.note,rf.filed_at "
                "FROM rotation_filings rf JOIN elements e ON e.id=rf.element_id JOIN shots s ON s.id=rf.shot_id JOIN actors a ON a.id=rf.actor_id "
                "WHERE e.production_id=? ORDER BY rf.id", (production_id,))],
            "scenes": scenes,
            "open_conflicts": sum(1 for scene in scenes for c in scene["conflicts"] if c["active"] and c["status"] == "open"),
            "exempted_conflicts": sum(1 for scene in scenes for c in scene["conflicts"] if c["active"] and c["status"] == "exempted"),
        }

    def snapshot(self) -> dict:
        return {
            "users": [dict(r) for r in self.conn.execute("SELECT id,name,role FROM users ORDER BY id")],
            "productions": [dict(r) for r in self.conn.execute("SELECT * FROM productions ORDER BY id")],
            "scenes": [dict(r) for r in self.conn.execute("SELECT * FROM scenes ORDER BY production_id,narrative_order")],
            "shots": [dict(r) for r in self.conn.execute("SELECT * FROM shots ORDER BY scene_id,narrative_order")],
            "actors": [dict(r) for r in self.conn.execute("SELECT * FROM actors ORDER BY id")],
            "character_cast": [dict(r) for r in self.conn.execute("SELECT * FROM character_cast ORDER BY id")],
            "shot_performances": [dict(r) for r in self.conn.execute("SELECT * FROM shot_performances ORDER BY id")],
            "rotation_filings": [dict(r) for r in self.conn.execute("SELECT * FROM rotation_filings ORDER BY id")],
            "conflicts": [dict(r) for r in self.conn.execute("SELECT * FROM conflicts ORDER BY id")],
            "adjustment_plans": [dict(r) for r in self.conn.execute("SELECT * FROM adjustment_plans ORDER BY id")],
            "exemptions": [dict(r) for r in self.conn.execute("SELECT * FROM exemptions ORDER BY id")],
        }
