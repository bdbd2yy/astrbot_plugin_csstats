# astrbot_plugin_csstats

AstrBot 的 CS 战绩查询插件，支持 5e、完美平台与官匹查询，并输出战绩图片卡片。此次插件改进全由ai生产。

## 功能特性

- 支持 5e / pw / mm 三个平台查询
- 支持账号绑定与按最近第 N 场查询
- 支持生成战绩图片卡片
- 支持 LLM 战绩点评
- 支持识别同队已绑定玩家，并指出本局“最菜队友”
- 使用 SQLite 保存绑定信息，支持同一 QQ 多平台绑定
- 保留 `@filter.command(...)` 的同时，增加 slash 命令兜底监听，服务器环境更稳定

## 当前实现说明

### 指令入口

插件目前保留以下命令：

- `/bind`
- `/match`
- `/cs_help`

同时增加了被动监听兜底：

- 当平台适配器没有把消息正确识别成 command 时
- 只要收到的是明确的 slash 指令
- 仍然会进入插件逻辑

当前监听是**保守模式**，只兜底这些白名单命令，不会去匹配普通自然语言聊天。

### 平台说明

- `5e`：使用 5e 玩家名绑定与查询
- `pw`：使用完美 App 用户名绑定与查询
- `mm`：查询官匹数据，但复用 `pw` 的绑定信息，因此需要先绑定 `pw`

## 安装方式

将插件放入 AstrBot 插件目录后，安装依赖：

```bash
pip install -r requirements.txt
```

依赖如下：

- `aiohttp`
- `aiosqlite`
- `tenacity`

## 使用方法

### 1. 绑定账号

```text
/bind 5e 玩家名
/bind pw 完美App用户名
```

示例：

```text
/bind 5e ExamplePlayer
/bind pw ExampleUser
```

说明：

- 5e 绑定使用 **5e 游戏名称**
- pw 绑定使用 **完美世界 App 用户名**
- `mm` 不单独绑定，查询时复用 `pw` 绑定信息

### 2. 查询战绩

```text
/match 5e 1
/match pw 2
/match mm 1
/match pw @某群友 3
```

说明：

- 最后一个数字表示最近第几场，`1` 表示最近一场
- 可以直接查自己，也可以在群里 `@` 已绑定的群友
- 如果 `@` 到 bot，本质上仍查询发送者自己
- `mm` 查询前，请先完成 `/bind pw ...`

### 3. 查看帮助

```text
/cs_help
```

## 查询结果

查询成功后，插件会：

1. 拉取对应平台比赛数据
2. 生成结构化战绩信息
3. 调用 LLM 生成点评
4. 渲染战绩图片卡片
5. 发送图片

如果检测到同队中有其他已绑定玩家，还会附加组排提示。

## 图片卡片说明

当前图片卡包含：

- 地图、时间、模式
- Rating / KD / ADR / RWS(WE)
- Elo 变化
- 本局比分
- 双方玩家表格
- LLM 点评
- 组排提示

最近已确认的展示规则：

- 所有数值统一保留到小数点后两位
- 比分展示为玩家视角的大比分
- 胜利绿色、失败红色、平局灰色
- RWS / WE 低于 `8` 标红，高于 `10` 标绿
- 5e 比分优先读取真实返回字段：
  - `data.main.group1_all_score`
  - `data.main.group2_all_score`

## 数据存储

插件当前使用 SQLite 保存绑定信息，表结构核心字段为：

- `qq_id`
- `platform`
- `player_name`
- `domain`
- `uuid`
- `updated_at`

特点：

- 支持同一 QQ 绑定多个平台
- 默认优先读取最近一次绑定的平台
- 兼容旧 JSON 数据迁移到 SQLite

## 目录结构

```text
astrbot_plugin_csstats/
├─ main.py
├─ requirements.txt
├─ core/
│  ├─ plugin_logic.py
│  ├─ ai_logic.py
│  ├─ report_generator.py
│  ├─ platforms/
│  │  ├─ fivee_logic.py
│  │  ├─ pw_logic.py
│  │  └─ mm_logic.py
│  ├─ prompts/
│  └─ templates/
└─ models/
   ├─ player_data.py
   └─ match_data.py
```

## 开发与调试建议

### 1. 先确认 API 完整返回，再写字段处理

这是这次修改里最重要的经验之一。

不要先根据猜测去写字段路径，推荐顺序：

1. 先抓接口完整返回 JSON
2. 先确认顶层结构和真实字段名
3. 再写解析逻辑
4. 最后再补 fallback

这次 5e 比分问题就是这样定位出来的：

- 最近比赛列表里存在 `group1_all_score/group2_all_score`
- 比赛详情 `data.main` 里也直接有这两个字段
- 所以不应该从玩家明细里猜比分

### 2. 生产环境不要只依赖 `@filter.command(...)`

本插件已经增加 slash 指令兜底监听，避免在 Linux / Docker / 不同适配器环境下出现“消息发了但插件完全没触发”。

### 3. 图片发送优先走共享目录

当前更稳的方案是：

1. 把图片写到共享目录
2. 使用 `Image.fromFileSystem(...)` 发送

推荐共享目录：

- `/AstrBot/data/temp`
- `/AstrBot/data/cache`

### 4. 发图失败时分层排查

建议按这个顺序看：

1. 业务数据是否正确
2. 图片是否真的生成成功
3. AstrBot 是否把图发出去了
4. NapCat / QQ 是否接收成功

## 已知事项

- `mm` 查询依赖平台侧可用登录态；如果上游 token 失效，会直接提示平台暂时不可用
- 图片渲染链路依赖 AstrBot 的 T2I 能力；生产环境建议自建 T2I 服务

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [astrbot_plugin_battlefield_tool](https://github.com/SHOOTING-STAR-C/astrbot_plugin_battlefield_tool/tree/master?tab=readme-ov-file)

## 开源协议

本项目采用 [GNU Affero General Public License v3.0](LICENSE)
