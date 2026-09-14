# 自定义标签接口与部署

## 部署

前端通过以下接口接入。

## 公共约定

- 所有接口位于 `/v1/tags`，需要现有登录认证。
- 新接口的 ID 响应均为十进制字符串；请求 ID 在 OpenAPI 中声明为 `string | integer`，支持十进制字符串或整数，后端在进入业务层前统一转换为整数。这一约定同样适用于路径 ID、`tag_ids`、帖子搜索及书单筛选的 `include_tag_ids` / `exclude_tag_ids` 数组。前端请使用字符串保存和提交 BIGINT ID，避免 JavaScript Number 丢失精度；非法 ID 返回 422。
- `target_type` 为 `thread` 或 `booklist`。
- 时间均为 UTC；七天期限从提议提交时开始，不依赖通知是否成功。
- 分类：1 癖好、2 作品、3 角色、4 特质、5 情节、6 背景、7 玩法。
- 原生标签不参与自定义分类和关系图谱，但占用帖子 12 个名额。
- 当前自定义票数始终读取数据库；前端自行决定高亮，搜索不按票数排序。

## 当前用户的管理身份

前端通过 `GET /v1/meta/role` 查询**当前登录用户**的管理身份，无需请求体或用户 ID 参数：

```json
{
  "is_management_member": false,
  "is_bot_admin": false
}
```

| 字段 | 含义 |
| --- | --- |
| `is_management_member` | 当前用户是否拥有**主服务器**配置的 `management_role_id` 身份组 |
| `is_bot_admin` | 当前用户的 Discord ID 是否在 `bot_admin_user_ids` 配置中 |

两项独立判断，可以都为 true、都为 false，或仅一项为 true；BOT 管理员不会自动被标记为管理组成员。管理组字段不代表其他服务器的身份，也不表示当前用户是某个帖子的作者或某个书单的所有者。

前端可根据这两个身份决定是否显示管理入口，例如只有 `is_bot_admin=true` 时显示标签池维护入口。具体操作接口仍执行原有权限校验，按钮显示不能替代后端授权。

未登录返回 401。用户不是主服务器成员时，`is_management_member=false`，BOT 管理员身份仍按配置返回；未配置管理组身份组时也返回 false。成员查询暂时失败或服务未初始化返回 503，前端应提示或重试，不要将查询失败当成确定没有管理身份。响应包含 `Cache-Control: private, no-store`，不要跨用户缓存结果。

## 标签池与管理

以下路径均接在 `/v1/tags` 后，所有写接口均仅允许 BOT 管理员使用。

| 方法和路径 | 含义 |
| --- | --- |
| GET /categories | 分类值和中文名 |
| GET / | 标签池，支持 q、category、selectable、include_deleted、offset |
| GET /relations | 未删除标签之间的全部直接关系边 |
| POST / | 创建标签，成功返回 201 |
| PATCH /{tag_id} | 部分修改名称、分类、启用状态 |
| DELETE /{tag_id} | 软删除标签 |
| POST /{tag_id}/restore | 恢复标签实体 |
| PUT /{tag_id}/aliases | 完整替换别名 |
| POST /{tag_id}/relations | 添加关系 |
| DELETE /{tag_id}/relations/{kind}/{target_tag_id} | 删除关系 |

标签池每页最多 100 条，按分类、名称和 ID 排序。默认 `selectable=true`，只列出启用且未删除标签；`selectable=false` 包括停用标签。查询已删除记录需 BOT 管理员，并设置 `include_deleted=true`。别名可同时命中多个标准标签。

创建 `POST /v1/tags`，名称和分类必填，别名可选：

```json
{"name":"爱丽丝(BA)","category":3,"aliases":["Alice","アリス"]}
```

修改 `PATCH /v1/tags/123`，未传字段保持不变。`name`、`category`、`enabled` 均可省略，但传入时不能为 `null`；空对象 `{}` 也会被拒绝，返回 422。`{"enabled":false}` 是有效的停用请求。OpenAPI 中这些字段为可选且不可为 null。名称、分类和启用状态在同一事务中修改：

```json
{"name":"爱丽丝(蔚蓝档案)","enabled":false}
```

`enabled=false` 为停用，`true` 为重新启用。停用保留现有展示和搜索。已软删除标签必须通过恢复接口恢复，不能通过 `enabled` 绕过。

替换别名 `PUT /v1/tags/123/aliases`，空列表表示清空：

```json
{"aliases":["Alice","アリス"]}
```

软删除使用 `DELETE /v1/tags/123`，恢复使用 `POST /v1/tags/123/restore`，均无请求体。软删除结束现有绑定、隐藏搜索、结束待审核项。恢复仅恢复实体，不恢复绑定。分类与标准名唯一性包括软删除记录；冲突时返回 `deleted_tag_exists` 和原标签 ID，提示恢复。

添加关系 `POST /v1/tags/123/relations`：

```json
{"target_tag_id":"456","kind":"implies"}
```

`kind` 为 `implies` 或 `excludes`，删除上述关系使用 `DELETE /v1/tags/123/relations/implies/456`。包含关系禁止循环，互斥关系对称。前端自行计算层级与选择父标签，后端只校验最终选择的集合。

标准名进行 NFC 规范化和首尾空白清理，不自动翻译或识别同义词。

BOT 的 `/tag_manage payload` 仍保留命令形式，与 HTTP 拆分互不影响。例如 `{"operation":"create","name":"爱丽丝(BA)","category":3}`。支持 create、update、disable、enable、delete、restore、add_relation、remove_relation；已有标签操作携带 tag_id，关系操作再携带 target_tag_id 和 kind。命令仅允许配置中的 BOT 管理员使用，不会发布公共消息。

## 帖子和书单治理

### 路径怎么读

以下路径均省略了统一前缀 `/v1/tags`。花括号中的内容是占位参数，调用时需要换成实际值，不要保留花括号。

| 路径参数 | 含义与取值 |
| --- | --- |
| `{target_type}` | 内容类型：`thread` 表示帖子，`booklist` 表示书单 |
| `{target_id}` | 当类型为 `thread` 时使用 **Discord 帖子 ID**；当类型为 `booklist` 时使用书单 ID |
| `{tag_id}` | 标签池中标签实体的内部 ID，不是 `discord_tag_id` |
| `{proposal_id}` | 一条添加标签申请的 ID，由提议接口及申请列表返回 |
| `{binding_id}` | 某个标签挂到该内容上的**本轮绑定 ID**，由标签读取接口返回；不是标签 ID。解绑后重新挂标会产生新的绑定 ID |

`proposals` 表示添加标签的申请，`votes` 表示本轮投票，`audit` 表示操作记录。`review_queue=true` 是查询参数，不是路径的一部分，用于切换到作者／管理组的待审核队列。

下面路径中的 `tag` 是固定文字，不是需要替换的参数：`/tag/{tag_id}/audit` 查询标签实体的管理历史，例如重命名、停用和恢复；`/thread/{target_id}/audit` 或 `/booklist/{target_id}/audit` 查询该内容上的挂标、解绑、审核和投票历史。

### 接口路径表

| 方法和路径 | 含义 |
| --- | --- |
| GET /{target_type}/{target_id} | 原生标签、自定义标签、票数、本人投票与版本 |
| PUT /{target_type}/{target_id} | 作者或管理组替换全部自定义标签 |
| POST /{target_type}/{target_id}/proposals | 提议一个标签 |
| GET /{target_type}/{target_id}/proposals | 本人的申请历史 |
| GET /{target_type}/{target_id}/proposals?review_queue=true | 作者或管理组的待审核队列 |
| PUT /{target_type}/{target_id}/proposals/{proposal_id} | 同意或拒绝 |
| PUT /{target_type}/{target_id}/votes/{binding_id} | 设置本轮投票 |
| GET /{target_type}/{target_id}/audit | 管理组/BOT 管理员的操作记录 |
| GET /tag/{tag_id}/audit | BOT 管理员的标签池操作记录 |

### 完整请求示例

以下数字仅作示例，实际调用需使用接口返回或目标内容对应的 ID。

| 完整请求 | 含义 |
| --- | --- |
| `GET /v1/tags/thread/123` | 查看 Discord 帖子 123 的标签、票数和版本 |
| `PUT /v1/tags/thread/123` | 作者或管理组替换帖子 123 的全部自定义标签 |
| `POST /v1/tags/thread/123/proposals` | 为帖子 123 提议标签，请求体为 `{"tag_id":"456"}` |
| `GET /v1/tags/thread/123/proposals` | 查看我为帖子 123 提交的申请 |
| `GET /v1/tags/thread/123/proposals?review_queue=true` | 作者或管理组查看帖子 123 的待审核申请 |
| `PUT /v1/tags/thread/123/proposals/789` | 审核帖子 123 的申请 789，请求体为 `{"approve":true}` 或 `{"approve":false}` |
| `PUT /v1/tags/thread/123/votes/321` | 对帖子 123 的挂标轮次 321 投票，请求体为 `{"vote":1}`、`{"vote":-1}` 或 `{"vote":0}` |
| `GET /v1/tags/thread/123/audit` | 管理组／BOT 管理员查看帖子 123 的标签操作记录 |
| `GET /v1/tags/booklist/42` | 查看书单 42 自身的标签，不是查询书单内所有帖子的标签 |
| `POST /v1/tags/booklist/42/proposals` | 为书单 42 提议标签，其余审核、投票路径同样将 `thread/123` 换成 `booklist/42` |
| `GET /v1/tags/tag/456/audit` | BOT 管理员查看标签实体 456 的管理历史 |

### 请求体与操作规则

#### `version` 是什么

`version` 是后端返回的**当前标签集合的版本标记**，用于判断用户编辑期间，标签是否已被其他操作修改。它不是作品的版本号，也不是标签 ID。

之所以需要它，是因为 PUT 会替换全部自定义标签。例如你读取到标签 A、B，准备修改为 A、B、D；此时管理组已经添加了 C。如果直接保存你的集合，就会误删 C。携带读取时的 `version` 后，后端可以识别这种冲突并拒绝覆盖。

前端将它当作一个不透明字符串即可：**保存 GET 返回的值，PUT 时原样传回，不自行生成、解析或递增。** 即使标签列表为空，也必须使用 GET 返回的版本标记。每个帖子／书单分别保存自己的版本，不要跨目标复用。

#### 前端对接步骤

1. 打开编辑页时调用 `GET /v1/tags/thread/123`。书单对应 `GET /v1/tags/booklist/42`。
2. 从响应中保存 `version`，并用 `tags` 初始化编辑状态。响应结构示意如下，版本字符串仅为占位示例，实际值以接口返回为准：

```json
{
  "version": "后端返回的版本标记",
  "tags": []
}
```

3. 用户确认修改后，向同一路径发送 PUT，提交完整的目标自定义标签 ID 集合，以及打开编辑页时保存的版本：

```json
{
  "version": "后端返回的版本标记",
  "tag_ids": ["123", "456"]
}
```

4. 保存成功后，使用 PUT 响应中的 `tags` 和 `version` 更新页面状态；后续编辑使用这次响应的新版本。

#### 版本冲突时怎么处理

如果读取后发生了挂标、解绑或原生标签同步变更，旧版本提交会被拒绝，**本次替换不会生效**。后端返回 HTTP 409，错误体为：

```json
{
  "detail": {
    "code": "stale_version",
    "message": "标签已变化，请刷新后重试"
  }
}
```

前端应提示用户标签已变化，重新 GET 最新集合及版本，并让用户确认最终选择后再提交。可以保留本地编辑内容用于对比，但**不要仅替换成新 `version` 就自动重试旧集合**，否则仍可能覆盖别人刚完成的修改。

通过 `detail.code == "stale_version"` 识别版本冲突，不要把所有 409 都当成版本过期：数量超限、互斥关系等也可能返回 409，应展示相应错误原因。

#### 其他操作规则

完整替换不提交原生标签。交集保留票数，未传入的自定义标签解绑，新绑定开启零票轮次。纯删除允许处理历史超限或冲突；含新增标签的操作要求最终集合合法。`version` 仅用于完整集合替换；提议、审核和投票接口不需要此字段。

提议提交 `{"tag_id":"123"}`，审核提交 `{"approve":true}` 或 `{"approve":false}`。同目标同标签最多一条待审核申请，重复提议不延长期限。拒绝或手动删除后可立即重提。曾被投票移除的标签永久禁止普通用户重新提议，作者与管理组可直接添加。

投票提交 `{"vote":1}`、`{"vote":-1}` 或 `{"vote":0}`，分别表示赞、踩、撤票。重复请求幂等，改票同时更新双方计数。净负票超过 5 时自动解绑，包括作者和管理组添加的标签。旧轮次 ID 不可用于新轮次。

提议和审计分页使用 `offset`，每页最多 100 条。普通响应不返回他人的投票身份。管理组只查询其授权目标的操作记录，BOT 管理员可查询全部目标。

## 搜索与详情

帖子和书单统一使用 `include_tag_ids`、`exclude_tag_ids`，取值为内部标签 ID，不按来源拆分参数。帖子可在同一列表混合原生和自定义标签，`tag_logic` 的 AND/OR 对整个列表生效。书单按自身实际绑定匹配，不继承单内帖子的标签。旧的按名称筛选参数保留兼容；同时提供名称和 ID 条件时，两组条件共同约束结果。

公开和我的书单列表新增同名查询参数，以及 `tag_logic=and|or`；多个 ID 使用重复查询参数。帖子详情展示上下文、书单摘要和书单详情新增 `custom_tags`，包含标准名、分类、挂标轮次及正负票数。

别名用于标签池候选检索，选择候选后以 ID 筛选内容；不沿关系扩大匹配。停用标签的现存绑定仍能筛选，软删除标签不再命中。

## 后台任务与通知

BOT 每分钟处理到期申请，锁定目标后重新检查；容量、互斥、停用、权限等业务失败进入 `failed` 状态，并在 `reason` 返回原因。数据库或权限服务临时故障留待下轮重试。

提议通知持久化入库。BOT 优先私信作者，私信禁止时在原帖发送不含具体待审核标签的提醒；书单或不可写帖子使用内部 notifications。临时网络故障指数退避重试。Discord 发送与数据库提交无法组成原子事务，极端崩溃窗口下提醒可能重复，业务申请与挂标不会因此重复。

内部通知新增 `type=tag_review`，返回 `target_type`、字符串 `target_id` 和 `proposal_id`，`thread` 可为空。新增 `POST /v1/notifications/{notification_id}/read`，只标记当前用户拥有的通知。
