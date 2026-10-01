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

`crontab -e` 添加（北京时间 9:00 前送达）：

```cron
CRON_TZ=Asia/Shanghai
0 */3 * * *    cd /opt/day60sMsg && ./venv/bin/python main.py --crawl >> logs/cron.log 2>&1
45 8 * * *     cd /opt/day60sMsg && ./venv/bin/python main.py --send   >> logs/cron.log 2>&1
```

第一行：每 3 小时抓取一轮入候选池（可自行加密到每小时，开销极小）。
第二行：每天 8:45 补抓最新内容并策展发送，留出 15 分钟余量保证 9:00 前送达。

> 旧版 cron 不支持 `CRON_TZ` 的话，把服务器时区设为东八区（`timedatectl set-timezone Asia/Shanghai`）后去掉该行。

## 接入 X（Twitter）大佬动向

X 官方 API 每月 200 美元起，直接爬取需要登录态且容易封号。免费且稳定的方案是**自建 RSSHub**，把自己 X 账号的登录凭证转成 RSS：

```bash
# 1. 在服务器上跑一个 RSSHub
docker run -d --name rsshub -p 1200:1200 \
  -e TWITTER_AUTH_TOKEN=<你的auth_token> \
  diygod/rsshub

# 2. 获取 auth_token：浏览器登录 x.com → F12 → Application → Cookies → 复制 auth_token 的值

# 3. config.yaml 中把 X·Elon Musk / X·Sam Altman 的 enabled 改为 true，
#    url 保持 http://127.0.0.1:1200/twitter/user/<用户名> 即可
```

RSSHub 的输出就是标准 RSS，本程序无需任何改动。想追踪其他人，复制一个 source 条目改用户名即可。

## 调整内容

- **版块条数**：`config.yaml` 的 `budget`（默认国际 6 / 国内 6 / 科技人物 4，共 16 条，约 60 秒读完）
- **增删数据源**：`sources` 下增删条目；任意 RSS 都能接（`type: rss`），`section` 决定它进入哪个版块
- **AI 供应商**：任何 OpenAI 兼容接口均可，改 `llm.base_url` / `model`（智谱 `glm-4-flash` 免费额度大，DeepSeek `deepseek-chat` 便宜）
- **挑选项规则**：`llm.py` 顶部的 `SYSTEM_PROMPT`（编辑价值判断标准）

## 目录结构

```
main.py            主流程与命令行入口（--crawl 入池 / --send 策展发送）
sources.py         多源抓取、归一化、时间窗过滤
pool.py            候选池（data/pool.json）：分时段累积、去重、过期清理
llm.py             AI 策展（挑选/翻译/摘要）+ 无 key 降级
email_builder.py   HTML/纯文本邮件渲染（全内联样式）
sender.py          QQ 邮箱 SMTP 发送
store.py           已推送去重（data/seen.json，滚动 7 天）
config.yaml        全部配置（可提交）
config.local.yaml  私密配置覆盖（自动生成，已 gitignore）
logs/              按月滚动日志
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
- **抓取频率**：默认每 3 小时一轮；想更密集可改成每小时（`0 * * * *`），9 源一轮只需几秒、流量以 MB 计
