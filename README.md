# 影视拍摄连续性管理

项目使用 Python 标准库、SQLite 和 `http.server` 管理非线性拍摄中的连续性。场次与镜头分别记录叙事顺序和拍摄顺序，角色、服装、道具、伤痕状态按叙事链检查，冲突可由调整方案或正式豁免处理，镜头只有在无未处理冲突时才能锁定。演员安排同样纳入连续性：角色登记主演和允许的替身，镜头标明实际出演者，替身出镜与主演轮换按规则生成冲突。

## 运行与测试

```bash
python app.py
python -m unittest discover -s tests -v
```

默认端口 `8115`，页面 <http://127.0.0.1:8115>。首次启动创建“雨夜追踪”示例，其中拍摄顺序与叙事顺序相反，并生成一个伤痕回退冲突和一个对白镜头替身冲突。数据库和端口可分别用 `CONTINUITY_DB`、`PORT` 指定。

## 连续性算法

每个元素选择一种规则：

- `stable`：沿叙事顺序状态必须一致。
- `monotonic`：使用 `numeric_value` 比较，数值不能下降，适合伤痕、污损或破坏程度。
- `allowed`：只有预先登记的状态转移才能通过。

检测按叙事顺序执行，与剪辑和拍摄顺序无关。调整方案必须由制片人或场记提出、由另一位审片人批准；批准后写入镜头状态并重新检查。也可以为确实需要保留的冲突写入豁免理由。锁定会再次检查场次，豁免之外的活跃冲突会阻止锁定，锁定后直接改状态会失败。

## 演员安排

角色（`kind` 为 `character` 的元素）登记主演（`lead`）和允许的替身（`standin`），镜头按角色标明实际出演者，镜头分为对白（`dialogue`）和动作（`action`）两类。检查规则：

- 替身可以完成动作镜头；对白镜头出现替身生成 `standin_in_dialogue` 冲突。
- 相邻镜头之间主演换人（主演轮换）必须经审片人备案，否则生成 `lead_rotation` 冲突；备案后冲突消除。
- 出演者未登记为该角色的主演或替身，生成 `unregistered_performer` 冲突。

修改选角、出演者或镜头类型后，涉及的镜头自动回到待处理（`planned`）并重新检查所在场次；已解决或已豁免的冲突、调整方案、豁免和备案记录全部保留，可在 `/api/state` 快照中查看。`POST /api/shots/{id}/performances` 的 `actor_id` 传 `0` 表示清除该角色的出演记录。

## 主要接口

- `POST /api/users`、`POST /api/productions`
- `POST /api/productions/{id}/scenes`、`POST /api/scenes/{id}/shots`
- `POST /api/productions/{id}/elements`、`POST /api/elements/{id}/transitions`
- `POST /api/productions/{id}/actors`
- `POST /api/characters/{id}/cast`、`POST /api/characters/{id}/cast/remove`
- `POST /api/characters/{id}/filings`
- `POST /api/shots/{id}/performances`、`POST /api/shots/{id}/shot-type`
- `POST /api/shots/{id}/states`、`POST /api/scenes/{id}/check`
- `POST /api/conflicts/{id}/plans`、`POST /api/plans/{id}/review`
- `POST /api/conflicts/{id}/exemptions`
- `POST /api/shots/{id}/lock`
- `GET /api/productions/{id}/continuity`
