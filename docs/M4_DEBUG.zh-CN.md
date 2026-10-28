# M4日志与验收：beta.20 / schema=9

一个Debug开关控制M4四路日志。默认关闭；关闭时不启动SESSION、金额探针或详细观察。开启、或Debug已开启时读档，创建新会话；SESSION不是每艘船、每笔交易各写一次。船只的简要游戏日志簿独立保留。

启动参数需包含`-scriptlogfiles`。日志目录为游戏用户目录中的logs/MulesExtended；每路追加写入，不自动覆盖旧日志。普通/SETA共用这四路文件，依据每行seta/factor分段，且应把跨切换点的搜索单列。

| 文件 | 内容 |
|---|---|
| m4-lifecycle.txt | 配置、状态、心跳、时钟、最终RESULT和边界 |
| m4-search.txt | 候选、预筛、刷新、分级、扩展、搜索结果和计数 |
| m4-orders.txt | 提交复核、QUEUE、ORDER_CREATED、原生通知和订单观察 |
| m4-economy.txt | 模组预订创建/接管/释放、净量、余货回收 |

每次会话四路均应有schema=9、mod=0.1.0-beta.20、internal=682的SESSION、SESSION_META及SESSION_MONEY_PROBE。探针应为rawCt=1223、moneyProbeCr=12.23、moneyProbeValid=1（适配器显示True）。它只验证这个日志换算表达式，不能证明真实交易金额、所有字段或所有事件完整。strategy=beta.19表示贸易规则版本。

常规行包含t、session、seq、ship/object/pilot、attempt、voyage、事件与wall/seta/factor。seq跨四路共用，头部使用0；合并后按session+seq检查普通事件连续性。使用attempt关联搜索，voyage关联航次，order/trade关联原生身份；不能按行相邻或时间相近猜测归属。seq连续也不能证明未生成的事件存在，仍须核对事件配对和明细数量。

## 装载率阶段

| 记录 | 含义 |
|---|---|
| BEST / END / DRAFT_LEG，stage=search | 搜索时的最优方案或草案 |
| SEARCH_CONSTRAINTS，search* | 搜索结束快照，提交修复不会回写 |
| REPAIR，before* / candidate* / accepted | 修复前与尝试修复后的方案；accepted不等于最终订单创建成功 |
| RESULT，search* | 原搜索快照；无快照时snapshot_unavailable |
| RESULT，committed* | 仅成功创建订单时计算；失败为null/not_committed |

committedLoadPercent是最终订单计划体积/容量，orders_created不是实载或已交付。实际货物转移看NATIVE_TRADE、ORDER_OBSERVED和ORDER_OUTCOME。百分比保留两位并截断，原始体积和容量才是复算依据。capacityBasis=last_ware_whole_unit_limit说明分母是最后一笔货物的整件最大容积；混合货物舍入可能略超100%，不截平原始证据，也不据此认定实际超载。

schema=8的observedLoadPercent不能直接当成schema=9的committedLoadPercent；旧volume/capacity/lots也不能不分阶段混用。旧日志按当时schema解读。

## 金额、计数与失败

金额以整数ct为核对依据，100ct=1Cr；Cr为固定两位文本，逐对核对。NATIVE_TRADE的valueCt=unitPriceCt×transferred属于该原生交易身份的累计观察，不是本回调新增现金流；started/completed可能报告同一交易。deltaTransferred相对观察缓存，缓存也可能由轮询推进，不能直接相加声称精确收支。内部免费交易另看internal/buyfree。

QUEUE与ORDER_CREATED按session、船、attempt/voyage、订单身份、货物、数量配对；历史订单用ORDER_ADOPTED，不要求有本会话ORDER_CREATED。原生终止通知不保证说明原因，cause=native_reason_not_provided按未知原因处理。订单消失但没有终止通知时为not_observed；不能自动写成失败或成功。transferState只表示已观察到的转移事实。

LOGGER_ERROR保留事件名、消息字段或字段清单及可用原始ct；字段清单表示排查范围，不是精确底层故障定位。无此错误也须检查正文、序号和配对。COUNTS_SUMMARY的countsValid表示计数自洽；detailBodiesComplete还要求完整枚举、同会话及汇总前无格式失败，且detailScope=planner_enumeration。它不涵盖后续提交/订单/原生回调，也不确认文件系统持久化；failureScope=through_summary说明时间边界。读取跨会话及中止轮次时分开统计。

HEARTBEAT的invocationAlive=not_observable不等于调用仍在运行。WATCH_READY的nativeDelivery=unverified表示已设置观察器，真正回调投递须由NATIVE_ORDER/NATIVE_TRADE证实。PHASE_TIME、YIELD、elapsed、等待和t均为游戏时钟跨度，包括让出与调度；不是CPU耗时，不能推断SETA计算优先级或漏算船只。

## 本版短测步骤

1. 确认正常退出游戏后安装备份过的beta.20。保留旧存档4、2及历史日志，不在游戏运行中移动日志。使用新槽位建立本轮基点和终点。
2. 载入可继续的存档，先保持SETA关闭。无需在载入瞬间抢暂停，先让初始M4计算和界面恢复稳定。确认“从AI派系中收集”选项消失。
3. 通过唯一Debug选项开启记录。若载入时已经开启，会自动建新会话；为了清楚的新边界，可在稳定后关一次再开启。等四路SESSION和初始化写出后再暂停，另存新基点，记录槽位和游戏时间。Debug保持开启。
4. 解除暂停，以普通速度运行到至少一艘船完成一轮搜索并新建订单；尽量等一笔采购或交付触发原生交易通知。然后开启SETA，继续运行几分钟，使新会话包含两种速度及多轮M4记录。先Debug、后SETA；暂停时不会推进模拟。
5. 结束时先关闭SETA，再暂停，保持Debug开启另存终点。记下槽位、时间和速度切换；之后关闭Debug，正常退出游戏。不要覆盖旧证据。
6. 检查本会话四路有效头、12.23探针、代表性候选、ORDER_CREATED↔QUEUE、RESULT分阶段装载率、原生事件及LOGGER_ERROR；按原始ct复算。没有发生修复时，不要求出现REPAIR，可在以后报价竞争时验证；不能伪造游戏验收覆盖。

本版静态、适配器、结构比较和包体检查已通过；2026-10-28存档4→5普通速度、5→6 SETA的原生日志验收也已通过，见[验收记录](NATIVE_BETA20.zh-CN.md)。以上步骤供复现或后续修改验收使用。beta.16曾按用户决定直接长测，这属于历史安排，不能当作本版原生短测已经通过。

M1–M3日志仍是继承的旧格式，部分旧输出按chance采样、使用旧文件且无M4会话/ct保证；本轮扫描保留其代码，不把M4的完整性承诺扩展到它们。
