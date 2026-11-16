# 临港 · 港站晨报定时分发

把昨日计划到发、延误分位数与交接班晨报串起来，由真实worker到点生成站内收件箱回执，保留冻结来源。

## 场景与工作流

临港联运运营北站、南站和东港三个港站，每天约150条班次记录。现行工作流是值班员导出 `临港到发_20261114.xlsx`，分析员按日期筛选后手算延误，负责人再把表格转发给下一班。痛点是跨午夜班次归错日、未到发被当准点、重复转发后分不清同一口径。上游为港站计划/实际到发记录；本项目替代日界归档、晨报试算和定时分发；下游为站内值班室交接班。

三个核心实体：Arrival到发记录、Subscription晨报订阅、Receipt分发回执，另有用户和审计。六页：到发台账、晨报预览、订阅计划、分发回执、增量同步、同步演练。回执支持值班复核与下载，原始记录修订不改变已分发晨报。

增量同步解决“每天全量抄台账、抄取期间的改站会漏传”：每个港站可独立订阅，接收端按变更序号拉取。首次全量是某一时刻的站台账快照，与之后的增量事件共用同一条序号链，因此全量转增量无缝衔接。班次改站会产生两条语义：从原港站撤回、在新港站补全；同一班次多次修订逐个入链、按先后生效。接收端可据此重建当前台账。

|角色|权限|典型动作|
|---|---|---|
|admin|全部；独占订阅配置/增量订阅/演练/值班复核/审计|设定08:30晨报、创建接收端令牌并核对回执|
|analyst|到发录入/修订、晨报试算、读取计划回执与增量进度、导出|补录跨午夜班次|
|viewer|业务只读与回执导出|下载冻结晨报、查看各端同步进度；后端拒绝写入/计算|

## 快速启动

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

前端 [localhost:18212](http://localhost:18212)，API [localhost:19212/docs](http://localhost:19212/docs)，PostgreSQL映射17712。账号 `admin@demo.local`、`analyst@demo.local`、`viewer@demo.local`，密码均 `Demo123!`。种子为3账号、150条昨日计划到发、3条暂停订阅、3个启用中的港站增量订阅（令牌需在页面创建或轮换后获取）；不会自动对外发送。自然编号幂等，重复运行不覆盖人工修订；种子日期为首次启动的昨日，后续日需业务录入新记录。

独立的命令行接收端（不依赖浏览器，只用标准库）：

```sh
docker compose exec -T backend python -m scripts.receiver_demo \
  --url http://backend:8000 --bootstrap 北站,南站,东港 --db-dir /artifacts/receivers --rounds 1
# 或显式令牌，一个进程多个接收端，各自 SQLite 台账
docker compose exec -T backend python -m receiver.cli \
  --url http://backend:8000 --token psync_... --db /artifacts/北站.sqlite --mode run
```

也可以在“增量同步”页创建订阅取令牌，或用 `docker compose --profile receiver up -d receiver` 常驻演示接收端。增量自检：`python -m scripts.sync_selftest --url http://backend:8000`，覆盖首次全量、改站往返、连续修订、确认前重取、落库后中断续跑、快慢端独立推进与对账。

## 一日业务剧本

1. 08:00 viewer 查看LG-001至LG-150，核对未到发为空。
2. 08:05 analyst 录入 `LG-151`，北站，计划2026-11-14 23:55，实际2026-11-15 00:05，交接说明为跨午夜。
3. 08:10 在晨报预览选择2026-11-14和北站，检查该班次仍归14日、延误10分钟。
4. 08:15 admin 创建“北站08:30值班晨报”，站内收件箱“北站值班室”，每日08:30，启用。
5. 演示时可把“首次到点”设为未来10秒；不需要任何手动发送按钮。
6. 08:30 worker每秒检查到点订阅，冻结昨日来源并写入回执。
7. 08:31 刷新分发回执，核对P50/P95、完成/未到发人数和来源checksum。
8. 08:35 admin 写入“跨日班次已核对”复核意见；viewer下载 `port-digest-<业务日>-<回执id>.json`。
9. 增量同步：admin 在“增量同步”页新建北站订阅、记下只显示一次的接收端令牌，用命令行接收端首次拉取得到完整北站台账；随后把一条班次改到南站、对另一条连改两次实际时间，接收端增量拉取后自动撤回/补全并按先后应用修订。
10. 演练：在“同步演练”页新建演练得到快端A/慢端B，两端各点“追平”后依次点四个变更步；只让快端“同步 1 批”，慢端落后但不阻塞。对慢端点“落库后中断”（已落库、未确认），服务端仍保留待确认批次，再点“断点续跑”，两端对账均显示一致；“断开/重连”可演示断连期间变更堆积。

## 计算与调度口径

**归档**按计划到发时间转Asia/Shanghai后的自然日，而非实际到达日。后端时间输入必须有偏移（如+08:00），统一保存UTC；前端datetime-local明确按上海解释。实际可空，表示未到发。时间差超过3天拒绝，提示核对。

**延误**=max(0,实际−计划)分钟，提前到达记0。已到发才进入延误率与分位数分母；未到发单独计数。P50/P95使用排序后位置(n−1)p的线性插值；没有已到发样本时分位数、均值和延误率为null，页面显示“未定义”。输出带完整选中来源、口径版本与SHA256 checksum。

**到点调度**由worker每秒扫描UTC next_run_at，Redis锁串行化扫描，数据库用行锁和唯一约束 `(subscription_id, report_day)` 防止同订阅同报告日重复入箱。首次时间可设未来数秒进行真实调度验证；以后按Asia/Shanghai每日HH:MM安排。到点或服务中断恢复时只生成“执行时刻的最新昨日”一份，不补历史天；同日再次到点只推进next_run_at，不新增回执。暂停订阅不执行。修订订阅如果未填写首次时间，会重新安排下一个每日时刻；恢复暂停也按此规则。

**增量同步口径**。到发记录每次写入都记一条不可变的变更事件（自增序号 `seq`、班次ID、修订号、旧/新港站、完整payload与checksum）；历史数据在启动时补一条基线事件。每个港站订阅有独立游标。接收端首次拉取时，服务端按当时最新序号冻结一份站台账快照（超出上限会切成有序批次），快照之后的事件按序号增量下发；快照与增量共用一个序号空间，所以首次全量与后续变更无缝衔接。改站事件对原港站表现为撤回、对新港站表现为补全；同一班次多次修订按序号先后应用，修订号保证旧内容不会覆盖新内容。接收端逐批“拉取→落库→确认”，确认前批次一直保留在服务端并可按checksum原样重取，确认是幂等操作（重复确认返回duplicate）。落库与确认都在本端 SQLite，进程重启后按本端台账与确认游标继续，既不漏变更也不重复落库。多个接收端各自持游标，慢端不阻塞快端。接收端上报 `applied_seq/confirmed_seq` 与其台账，服务端在对应序号上与权威台账逐条比对，给出缺失、多出（应撤回）、字段不一致与校验失败清单。改站订阅或管理员重置会把generation加一，接收端看到新generation会先清空本端台账再重建，避免残留旧港站记录。演练数据存放在独立dataset，不进入真实台账或晨报。

晨报“送达”表示已提交站内数据库收件箱，不是SMTP邮件。回执保存冻结payload，DB提交后附加写共享产物卷；附加文件失败不影响DB回执和导出。值班复核仅管理员可做，一次复核不可覆盖，且不改变payload。支持单worker；唯一约束与锁防重复，但不声称对外消息的exactly-once。

## 工程、环境变量和开发

Python3.11/FastAPI/SQLAlchemy/PyJWT、原生ES modules/CSS、Nginx、PostgreSQL16、Redis7。默认frontend/backend/worker/db/redis五服务，各有健康检查；可选 `receiver` profile 提供一个常驻演示接收端。backend等db/redis、worker还等backend、frontend等backend。Redis实际提供调度锁；worker心跳 `/tmp/heartbeat`，DB/Redis/产物分别命名卷。Nginx `/api/` 去前缀代理后端根路由。

`.env.example`列出COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET，以及增量的RECEIVER_BASE_URL、SYNC_MAX_EVENTS、SYNC_PULL_LIMIT、SYNC_BOOTSTRAP_STATIONS。显式子网10.253.113.0/24规避当前机默认地址池耗尽，可按环境调整。演示凭据应在正式部署替换。清理本项目用 `docker compose down -v --remove-orphans`。

本地开发：启动db/redis，backend安装requirements，设置DATABASE_URL/REDIS_URL/JWT_SECRET，再运行 `uvicorn src.main:app --reload` 和 `python -m src.worker`；前端仍建议用Nginx同源代理。后端目录src按routes/controllers/services/models/schemas/middlewares/config/utils/constants分工；前端src按api/stores/pages/components/hooks/utils/constants/types/router分工。ORM首次建表，migrations保留实际DDL；`src/config/migrate.py` 只对既有库做幂等的新增列补齐（不删改列），其余迁移仍以SQL留档。接收端代码在 `backend/receiver/`（store/client/engine/cli），仅用标准库，可打包给站内单独部署。

枚举：后端 `src/constants/enums.py`、`src/schemas/business.py` 与前端 `src/constants/enums.js` 定义ROLES、STATIONS、RECEIPT_STATES；TIMEZONE固定Asia/Shanghai。共用表格、表单、状态标签、晨报摘要组件；表单hooks、HTML转义和日期工具；请求ID/安全响应头两个中间件，业务审计与全局错误恢复。测试与截图在 `../../test-artifacts/gb-3113/`，不放源码内。

范围：逐条录入/完整修订，无CSV导入、删除或分页；只有三个固定港站与上海时区。增量同步按港站（非按业务日）订阅，接收端令牌是独立凭据、只授予该订阅的数据，不能当值班员登录；服务端保留历史批次但不提供无限期历史回放，重置或改站会开启新generation。站内收件箱按名称记录，当前所有登录角色可读业务回执，无多租户邮箱隔离；不连接邮件、短信或外部IM。Google Fonts离线时字体回退。许可证MIT，见LICENSE。
