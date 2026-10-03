# day60sMsg · 每日要闻速览

每天早上 9 点自动抓取全球新闻，**AI 挑选最有价值的信息**，生成一封 60 秒能读完的简报邮件发送到 QQ 邮箱。邮件分为「国际要闻 / 国内动态 / 科技·大佬动向」三个版块，每条只有一句浓缩摘要。

## 工作流程

```
分时段预抓取（cron 每 3 小时：--crawl）——各源内容实时并入候选池 data/pool.json
  → 每天 8:45 定时任务（--send）：
      补抓一轮最新 → 合并候选池 → 24 小时窗口过滤 + 7 天滚动去重
      → AI 策展（挑选最有价值的条目、英文翻译成中文、每条 45 字内摘要、生成今日导读）
      → 渲染 600px 内联样式 HTML 邮件 → QQ 邮箱 SMTP 发送（9:00 前送达）
      → 标记已推送、清空候选池
```

候选池的意义：白天各时段的新闻都不会漏；单次抓取失败只少一轮补充，不影响当天发送；
9 点的任务不再依赖抓取结果，策展 + 发送几秒内完成。

单源失败只记警告不影响整体；未配置 AI key 时自动降级为按来源权重挑选（此时英文标题不翻译）。

## 快速开始

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip install -r requirements.txt
```

编辑 `config.yaml`，需要填两处：

| 配置项 | 说明 |
|---|---|
| `email.smtp.password` | QQ 邮箱 **SMTP 授权码**（不是 QQ 密码），获取方式见下 |
| `llm.api_key` | 智谱/DeepSeek 等 OpenAI 兼容接口的 key，留空则降级运行 |

> **推荐做法**：不要直接改 `config.yaml`，而是 `cp config.local.example.yaml config.local.yaml`，
> 把私密值填进 `config.local.yaml`。该文件已被 gitignore，既不会提交、也不会被 `git pull` 覆盖，
> 只填需要覆盖的字段即可，其余自动沿用 `config.yaml`。

本地验证：

```bash
python main.py --crawl        # 抓取一轮并入候选池
python main.py --preview      # 从候选池策展，生成 preview/preview.html（浏览器打开看效果，不发邮件）
python main.py --send-test    # 真实发送一封（验证 SMTP 配置），不记录去重、不清池
python main.py --send-if-needed  # 仅当今天尚未发送时执行发送（补偿 cron 用）
python main.py --weekly       # 汇总近 7 天存档，生成一周综述邮件（可选，见下）
python main.py --status       # 查看运行状态：上次发送、池规模、去重库、存档、报错
python main.py --dry-run      # 只抓取，打印各源统计，不写任何状态
python main.py --send         # 正式流程：补抓 → 策展 → 发送 → 去重记录（裸跑 python main.py 等价）
```

## QQ 邮箱授权码获取

QQ 邮箱网页版 → 设置 → 账号 → 开启「POP3/IMAP/SMTP 服务」→ 按提示发送短信 → 生成 16 位授权码 → 填入 `config.yaml` 的 `email.smtp.password`。

## 部署到服务器（Linux + crontab）

```bash
# 1. 上传项目到服务器，例如 /opt/day60sMsg
# 2. 安装
cd /opt/day60sMsg
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python main.py --preview   # 确认能跑通
```

`crontab -e` 添加（8:45 开始策展，**9:00 准点送达**，含失败补偿）：

```cron
CRON_TZ=Asia/Shanghai
0 */3 * * *    cd /opt/day60sMsg && ./venv/bin/python main.py --crawl          >> /dev/null 2>> logs/cron.err
45 8 * * *     cd /opt/day60sMsg && ./venv/bin/python main.py --send           >> /dev/null 2>> logs/cron.err
55 8 * * *     cd /opt/day60sMsg && ./venv/bin/python main.py --send-if-needed >> /dev/null 2>> logs/cron.err
06 9 * * *     cd /opt/day60sMsg && ./venv/bin/python main.py --send-if-needed >> /dev/null 2>> logs/cron.err
15 9 * * *     cd /opt/day60sMsg && ./venv/bin/python main.py --send-if-needed >> /dev/null 2>> logs/cron.err
```

- 第一行：每 3 小时抓取一轮入候选池（可自行加密到每小时，开销极小）
- 第二行：8:45 开始补抓与 AI 策展（给重试留足时间），完成后**等到 9:00 准点发出**（`email.send_at` 可改）
- 第三、四、五行：补偿发送——只有当天尚未成功发送时才会执行（8:45 任务意外失败时自动补救）
- 日志：完整运行日志在 `logs/YYYYMM.log`，`logs/cron.err` 只有报错堆栈
- **失败告警**：正式发送失败时，程序会给自己发一封告警邮件（每天最多一封）
- 运行锁：30 分钟 TTL，防止 cron 重叠；异常退出的锁会自动过期

> 旧版 cron 不支持 `CRON_TZ` 的话，把服务器时区设为东八区（`timedatectl set-timezone Asia/Shanghai`）后去掉该行。

## 接入 X（Twitter）动向（可选）

X 官方 API 每月 200 美元起，直接爬取需要登录态且容易封号。免费方案是**自建 RSSHub**，
把自己 X 账号的登录凭证转成 RSS（需 Node.js 22+，内存约需 150–250MB）：

```bash
# 1. 获取 auth_token：浏览器登录 x.com → F12 → Application → Cookies → 复制 auth_token 的值
# 2. 在服务器上跑一个 RSSHub
docker run -d --name rsshub -p 1200:1200 \
  -e TWITTER_AUTH_TOKEN=<你的auth_token> diygod/rsshub

# 3. config.yaml 中把 X·OpenAI 等源的 enabled 改为 true
```

RSSHub 输出就是标准 RSS，本程序无需任何改动。注意：用自己账号的 cookie 做自动化抓取
存在被风控的风险，建议使用小号。想追踪其他账号，复制一个 source 条目改用户名即可。

## 策展质量的精修机制

- **近重复折叠**：标题相似（共同长前缀或相似度 ≥0.72）的候选只保留发布最新的一条进提示词，同一事件的多源报道不再重复消耗注意力
- **跨版块防重**：提示词与结果回映射双层防护，同一事件不会在两个版块出现两次
- **跨天主题去重**：去重库同时记录条目 id 与主题标题（各留 7 天），近 2 天推送过的主题不再入选
- **续报标注**：提示词会带上近 2 天已推送主题，重大新进展可入选并自动标注[续报]
- **兴趣权重**：`config.yaml` 的 `interests: ["人工智能", "芯片"]`，同等重要性下 AI 优先挑选相关条目

## 头条推送（可选）

策展发送成功后，把最重要 N 条推到手机（`config.yaml` 的 `push` 段）：

```yaml
push:
  enabled: true
  top_n: 5
  channels:
    ntfy:                                  # 推荐：无需注册，手机装 ntfy App 订阅同名主题
      url: https://ntfy.sh/起一个别人猜不到的私有主题名
    # bark:                                # iOS Bark
    #   url: https://api.day.app/你的BarkKey
    # telegram:
    #   bot_token: "123456:ABC"
    #   chat_id: "你的chat_id"
```

推送尽力而为：任何渠道失败只记日志，不影响邮件。

## 周报（可选）

`python main.py --weekly` 会读取近 7 天的每日存档（`data/archive/`），由 AI 提炼 3~5 条本周主线发送综述邮件。想自动化可在 crontab 加一行（周日晚 20:30）：

```cron
30 20 * * 0    cd /opt/day60sMsg && ./venv/bin/python main.py --weekly >> /dev/null 2>> logs/cron.err
```

## 每日存档与自产订阅

每次正式发送后自动写 `data/archive/YYYY-MM-DD.html`（当天邮件）与 `data/feed.xml`（Atom 订阅），`data/archive/index.html` 是全部往期的索引。

## 调整内容

- **版块条数**：`config.yaml` 的 `budget`（当前国际 10 / 国内 10 / AI前沿 10，共 30 条）
- **输出长度**：`llm.max_tokens`（可选，防止部分模型默认输出过小导致 JSON 截断）
- **增删数据源**：`sources` 下增删条目；三种类型：
  - `rss` —— 任意 RSS/Atom
  - `60s` —— 每天60秒读懂世界聚合接口
  - `gnews` —— Google News 关键词检索（`query` 字段填主题或机构名，如 `Anthropic`）
- **AI 供应商**：任何 OpenAI 兼容接口均可，改 `llm.base_url` / `model`
- **挑选项规则**：`llm.py` 顶部的 `SYSTEM_PROMPT`（编辑价值判断标准）

## AI 前沿版块的数据来源

该版块由三类源混合构成（共 12 个，见 `config.yaml`）：

- **机构一手信息**：OpenAI 官方博客、Google DeepMind、Hugging Face 的官方 RSS
- **主题检索**：`gnews` 类型按主题检索全球报道——`Anthropic`、`artificial intelligence`、
  `AI regulation`。用于覆盖没有可用官方 RSS 的机构（如 Anthropic）与监管动态
- **媒体解读**：TechCrunch AI、The Verge AI、The Decoder、Simon Willison、量子位、MIT Tech Review

`gnews` 类型会自动剥离标题的 `- 媒体名` 后缀，并以真实媒体名作为来源显示；
每条候选受 `fetch.max_per_source` 限制（默认 8），避免主题检索条目过多而淹没其他版块。

要调整追踪方向，在 `config.yaml` 的 AI 前沿版块增删条目即可——复制一条 `gnews` 源改
`query`（如 `Anthropic`、`AI chips`、`open source LLM`），或加一个 AI 媒体的 `rss` 源。

## 目录结构

```
main.py            命令行入口：参数解析与调度（薄壳）
config.py          配置加载（config.yaml + config.local.yaml 私密叠加）
pipeline.py        执行编排：抓取、策展、日发送流水线、周报、状态
sources.py         多源抓取、归一化、时间窗过滤
pool.py            候选池：分时段累积、去重、过期清理
llm.py             AI 策展（挑选/翻译/摘要）+ 重试/故障转移 + 降级
email_builder.py   HTML/纯文本邮件渲染（全内联样式）
sender.py          SMTP 发送（重试）与告警邮件
push.py            ntfy/Bark/Telegram 头条推送
archive.py         每日存档（html+json）、索引页、Atom 订阅
store.py           去重库（条目 id + 主题标题，滚动 7 天）
state.py           运行锁、今日已发/告警标记
alerting.py        失败告警（读日志尾部发邮件，每日最多一封）
config.yaml        全部配置（可提交）
config.local.yaml  私密配置覆盖（自动生成，已 gitignore）
logs/              按月滚动日志（cron.err 只装堆栈）
preview/           --preview 输出
```

## 更新已部署的实例

```bash
cd /opt/day60sMsg && git pull && ./venv/bin/pip install -q -r requirements.txt
```

`config.local.yaml` 不在版本控制内，`git pull` 不会影响其中的授权码。

## 常见问题

- **邮件进了垃圾箱**：第一次去垃圾箱里点「这不是垃圾邮件」，之后即恢复正常
- **某天没收到**：看 `logs/YYYYMM.log` 和 cron.log；候选池为空且补抓也失败时程序不会发空邮件，退出码 1
- **中英文混杂**：降级模式（无 AI key）下国际源保留英文原文，配好 key 即自动翻译
- **想再发一次当天内容**：正式发送会去重并清池，重发用 `--send-test`
- **抓取频率**：默认每 3 小时一轮；想更密集可改成每小时（`0 * * * *`）
