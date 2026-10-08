# pluginDB

用于抓取 Zotero 插件的 release 信息，下载对应的 `.xpi`，解析其中的 `manifest.json`，并把插件元数据、release 元数据、多语言描述信息写入数据库。

## 背景

这个项目面向 Zotero 插件索引场景，默认合并上游 `zotero-chinese/zotero-plugins` 的 `src/plugins.ts` 和 `src/deprecated.ts`，覆盖活跃及旧版插件。同步任务会按插件定义抓取 GitHub release，区分正式版和预发布版，下载 `.xpi` / 插件 ZIP，计算 `md5`，解析插件兼容信息，并把结果落到本地 JSON 和数据库中。

2026-10 的新版清单使用固定历史 tag 配合 `discoverReleases: true`。同步器会保留这些历史版本，并从最近的发行版列表中发现最新正式版；按 `assetName` 选择指定附件，使用仓库别名合并重复来源。尚无正式发行版且没有历史选择器的来源记为 `pending_count`，不会导致整批失败；网络错误和历史版本抓取失败仍计入 `failure_count`。

当前实现特点：

- 支持直接读取上游 `plugins.ts`
- 支持用本地 `plugins.ts` 调试，避免每次都下载
- 支持 `customLink` 类型的自定义 XPI 下载地址
- 支持旧版插件包中的 `install.rdf`，当 `manifest.json` 不存在时自动回退解析
- 支持 `init` / `sync` 两种运行模式
- 支持通过 SQLAlchemy 切换数据库连接
- `plugins` 主表只保留插件级元数据，并保存人工归类 tags、GitHub stars、插件聚合下载量
- `plugin_releases` 表保存版本、兼容范围、xpi 路径、md5、release asset 下载量等 release 信息
- `plugin_locales` 表保存多语言或多来源的描述信息

## 数据模型

当前默认三张表：

- `plugins`
  保存插件级元数据，例如 `plugin_name`、`source_repo`、`homepage_url`、`author`、`tags`、`github_stars`、`download_count`
- `plugin_releases`
  保存 release 级元数据，例如 `tag`、`prerelease`、`asset_url`、`xpi_path`、`md5`、`download_count`、`manifest_version`
- `plugin_locales`
  保存插件描述类文本，目前主要是 `description`
  `source` 目前区分 `manifest` 和 `github_repo`

默认数据库是 SQLite，但代码通过 SQLAlchemy 接管连接，后续切到 PostgreSQL 之类的数据库时，只需要修改 `database_url`。

## 目录结构

```text
pluginDB/
  README.md
  pyproject.toml
  scripts/
    sync_plugins.py
    run_sync.sh
  src/plugindb_sync/
    artifacts.py
    config.py
    github_client.py
    plugin_source.py
    storage.py
    sync.py
  tests/
    ...
  data/
    cache/
    json/
    xpi/
    db/
```

说明：

- `data/` 和 `docs/` 已在 `.gitignore` 中排除
- `data/` 用于缓存、JSON 输出、XPI 文件和默认 SQLite 数据库

## 环境准备

推荐使用你现在的虚拟环境：

```bash
source ~/myenv/bin/activate
```

如果你是第一次在这个环境里跑项目，先安装依赖：

```bash
source ~/myenv/bin/activate
pip install -e .
```

当前主要依赖：

- Python 3.13+
- SQLAlchemy 2.x

## 运行

### 1. 直接读取线上 `plugins.ts`

```bash
source ~/myenv/bin/activate
python3 scripts/sync_plugins.py --root "$(pwd)" --mode sync
```

### 2. 使用本地 `plugins.ts` 调试

这个模式适合避免 `plugins.ts` 下载超时，或者调试特定插件列表。

```bash
source ~/myenv/bin/activate
python3 scripts/sync_plugins.py \
  --root "$(pwd)" \
  --mode sync \
  --plugins-file "$(pwd)/plugins.ts"
```

### 3. 初始化历史版本

如果你希望按 `plugins.ts` 中声明的 release 全量初始化，包括历史 tag：

```bash
source ~/myenv/bin/activate
python3 scripts/sync_plugins.py \
  --root "$(pwd)" \
  --mode init \
  --plugins-file "$(pwd)/plugins.ts"
```

新版清单在 `sync` 模式下同时处理历史版本与自动发现的最新正式版，首次即可补齐新插件，后续复用已下载的历史 XPI。没有 `discoverReleases` 标记的旧清单仍只处理 `latest`、`pre`、`custom`；需要补齐旧清单历史版本时使用 `init`。

指定 `--plugins-file` 时自动读取同目录的 `deprecated.ts`（如果存在）；也可用 `--deprecated-file PATH` 明确指定。自定义 `--plugins-url` 时，可用 `--deprecated-url URL` 指定对应旧版清单。`--active-only` 只处理活跃清单。

### 4. 指定数据库连接

默认会使用根目录下 `data/db/plugins.sqlite3`。如果你想明确指定，或者切换到其他数据库：

```bash
source ~/myenv/bin/activate
python3 scripts/sync_plugins.py \
  --root "$(pwd)" \
  --database-url "sqlite+pysqlite:///$(pwd)/data/db/plugins.sqlite3"
```

后续切换 PostgreSQL 时，示意如下：

```bash
python3 scripts/sync_plugins.py \
  --root "$(pwd)" \
  --database-url "postgresql+psycopg://user:password@127.0.0.1:5432/plugindb"
```

### 5. 使用 GitHub Token

完整清单包含 300 多个仓库，应设置 GitHub token 以避免匿名 API 限流。可在根目录 `.env` 中配置 `PLUGINDB_GITHUB_TOKEN` 或 `GITHUB_TOKEN`，也可通过 `--github-token` 显式传入：

```bash
source ~/myenv/bin/activate
python3 scripts/sync_plugins.py \
  --root "$(pwd)" \
  --github-token "$GITHUB_TOKEN"
```

## 输出内容

运行后默认会产生这些输出：

- `data/cache/plugins.ts`
  缓存本次实际使用的插件清单
- `data/cache/deprecated.ts`
  缓存本次实际使用的旧版插件清单
- `data/json/{plugin_id}.json`
  插件规范化 JSON
- `data/xpi/{plugin_name}/{tag}.xpi`
  下载后的插件包
- `data/db/plugins.sqlite3`
  默认 SQLite 数据库

运行过程中会直接打印标准输出日志，便于追踪下载和跳过行为，例如：

```text
action=download repo=demo/repo release=latest url=https://example.com/demo.xpi target=data/xpi/Demo/v1.2.3.xpi
action=skip repo=MuiseDestiny/ZoteroStyle release=custom@zotero-8 url=https://example.com/style.xpi target=data/xpi/ZoteroStyle/v5.8.6.xpi
action=skip_duplicate repo=MuiseDestiny/ZoteroStyle release=custom@zotero-9 url=https://example.com/style.xpi target=data/xpi/ZoteroStyle/v5.8.6.xpi
```

XPI 文件命名规则：

- 新版清单按 `data/xpi/{owner}/{repo}/{tag}/{assetName}` 存放，隔离同名仓库和同一 tag 的不同附件；自定义下载的目录 tag 使用 manifest 版本。数据库中的 `xpi_path` 指向实际文件，发布脚本可照常镜像整个目录。
- 以下扁平命名规则保留给未使用新格式的旧清单：
- 如果 tag 已经以 `v` 开头，不重复加前缀
- 例如 `v1.2.3` 保存为 `v1.2.3.xpi`
- 例如 `1.2.3` 保存为 `v1.2.3.xpi`
- `customLink` 下载完成后会按解析出的插件版本保存，例如 `v5.8.6.xpi`
- 如果数据库里已经记录了对应 release 且本地 XPI 文件存在，则会直接跳过下载，也不会重复计算 `md5`
- 同一次运行内，如果多个 release 指向同一个 XPI URL，只会下载和解析一次，后续 release 会复用第一次的结果
- 自定义 URL 每轮刷新，即使 manifest 版本未变化也会更新文件内容。

插件标签以 JSON 数组写入 SQLite 的 `plugins.tags`，Soil 导入器可以直接读取。旧数据库首次运行时自动新增该列并保留现有数据；单个插件抓取失败时保留其上次数据库记录。

XPI 元数据解析规则：

- 优先解析 `manifest.json`
- 如果没有 `manifest.json`，则回退解析 `install.rdf`
- `install.rdf` 中的 `localized description` 也会写入 `plugin_locales`

## 开发

### 运行测试

```bash
source ~/myenv/bin/activate
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### 查看 CLI 参数

```bash
source ~/myenv/bin/activate
python3 scripts/sync_plugins.py --help
```

### 常见开发入口

- `src/plugindb_sync/plugin_source.py`
  解析 `plugins.ts`
- `src/plugindb_sync/github_client.py`
  请求 GitHub API
- `src/plugindb_sync/artifacts.py`
  下载 XPI、计算 `md5`、读取 `manifest.json`
- `src/plugindb_sync/sync.py`
  整体同步流程
- `src/plugindb_sync/storage.py`
  SQLAlchemy 表结构和写库逻辑

## 调试建议

如果你在调试同步问题，优先按这个顺序排查：

1. 先用 `--plugins-file` 固定输入，避免把问题和网络下载混在一起
2. 再看 release 查询是否正常
3. 再看 `.xpi` 下载和 `manifest.json` 解析
4. 最后再检查数据库写入结果

如果只是想验证解析器是否识别了插件，可以先看：

- `data/cache/plugins.ts`
- `plugin_count=... success_count=... failure_count=...`

## 定时运行

项目本身是单次执行脚本，适合交给系统调度器每 5 小时跑一次。

`cron` 示例：

```cron
0 */5 * * * cd /absolute/path/to/pluginDB && /bin/zsh -lc 'source ~/myenv/bin/activate && python3 scripts/sync_plugins.py --root "$(pwd)" --mode sync'
```

如果需要把下载好的 XPI 发布到站点目录，可以在同步成功后执行发布脚本：

```cron
0 */5 * * * cd /absolute/path/to/pluginDB && uv run python3 scripts/sync_plugins.py --root "$(pwd)" --mode sync && uv run python3 scripts/promote_staging.py --root "$(pwd)" && scripts/publish_addons.sh /var/www/downloads/addons
```

`scripts/publish_addons.sh` 默认使用 `rsync -a --delete` 将 `data/xpi/` 镜像到目标目录。当前服务器的 FTP 目录是 `/var/www/downloads/addons`。
