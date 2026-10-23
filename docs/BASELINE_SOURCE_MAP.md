# 基线源码范围核对

对照为本地保存的原版 Mules, Supply and Warehouses Extended 6.60 发布归档。这里只记录文件范围，不附带旧版本安装包或历史提交。

初始基线运行时 XML 共 29 个：相对对照新增 13 个，修改 7 个，保留 9 个。另有根目录 `content.xml`，只调整本项目显示名称、描述、署名、日期和数字版本，扩展 ID 保留。

下表记录初始基线的源码清单。2026-10-23清理后删除了 `mule.lib.evaluate_tradeoffers.xml` 和 `quicksort.xml`，当前源码运行时XML为27个。安装包另外从默认英文源自动生成 `t/0001-l044.xml`，归档内共28个运行时XML；不维护第二份手写英文翻译。后续改动见[变更记录](CHANGELOG.zh-CN.md)和[清理说明](CLEANUP.zh-CN.md)。

导出的运行时代码与当前已安装基线逐文件核对一致，统一文本换行后无功能性改写。测试与校验脚本移除了本机路径依赖；打包工具、文档和 Git 配置是本次发布整理新增。

| 状态 | 文件 |
|---|---|
| 新增 | `aiscripts/mule.fleet.commit.xml` |
| 新增 | `aiscripts/mule.fleet.config.xml` |
| 新增 | `aiscripts/mule.fleet.discover.xml` |
| 新增 | `aiscripts/mule.fleet.home.xml` |
| 新增 | `aiscripts/mule.fleet.net.xml` |
| 新增 | `aiscripts/mule.fleet.offer.xml` |
| 新增 | `aiscripts/mule.fleet.plan.xml` |
| 新增 | `aiscripts/mule.fleet.price.xml` |
| 新增 | `aiscripts/mule.fleet.recover.xml` |
| 新增 | `aiscripts/mule.fleet.report.xml` |
| 新增 | `aiscripts/mule.fleet.run.xml` |
| 新增 | `aiscripts/mule.fleet.wave.xml` |
| 新增 | `md/mwex_capacity.xml` |
| 修改 | `aiscripts/mule.lib.evaluate_tradeoffers.xml` |
| 修改 | `aiscripts/order.assist.xml` |
| 修改 | `aiscripts/station_mule.xml` |
| 修改 | `aiscripts/supply_mule.xml` |
| 修改 | `aiscripts/travel_mule.xml` |
| 修改 | `t/0001-l086.xml` |
| 修改 | `t/0001.xml` |
| 保留 | `aiscripts/distrib_mule.xml` |
| 保留 | `aiscripts/lib.request.orders.xml` |
| 保留 | `aiscripts/mule.lib.debug.dump_tradeoffers.xml` |
| 保留 | `aiscripts/mule.lib.empty_cargo.xml` |
| 保留 | `aiscripts/quicksort.xml` |
| 保留 | `libraries/icons.xml` |
| 保留 | `md/mwex_patchnotes.xml` |
| 保留 | `md/mwex_setup.xml` |
| 保留 | `t/0001-l007.xml` |
