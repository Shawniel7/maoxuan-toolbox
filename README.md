# 思想工具箱 · Thought Toolbox

> 把你的当代困惑说给一套冷静的分析方法听 —— 抓主要矛盾、实事求是、持久战。

一个**纯静态网页**。你输入一段自己的烦恼(选择困难、精神内耗、比较焦虑、人际冲突……),它会匹配 1 - 3 个毛泽东著作中具备普适性的**分析方法**,返回:

- 原文短引(30 - 80 字)+ 指向公开权威版本的链接
- 现代译读 —— 把理论翻译到你当下的语境里
- 历史/现实类比故事
- 3 - 5 条可执行的行动清单(可勾选,状态存 localStorage)

**这不是政治宣传,也不是鸡汤。** 详见 [`about.html`](about.html) 里的免责声明。

## 运行

零依赖。需要一个本地 HTTP 服务器(因为 `fetch` 对 `file://` 有 CORS 限制):

```bash
# 任选一个:
npx serve .
# 或
python3 -m http.server 8080
```

然后在浏览器打开 http://localhost:8080 。

## 部署到 GitHub Pages

仓库里已经有 `.github/workflows/deploy.yml`。把项目推到 GitHub,去 Settings → Pages 把 Source 改成 "GitHub Actions",每次 push 到 `main` 就会自动发布。

## 技术栈

- 纯 HTML + CSS + Vanilla JS,无打包工具、无框架、无依赖
- 数据单文件:`data/entries.json`
- 匹配算法:关键词 + 标签打分(`js/matcher.js`)
- 可选 AI 模式:用户粘贴自己的 Anthropic API key 后,调用 Claude 做个性化译读 —— key 只存在浏览器 `localStorage`,不走任何后端(这个项目根本没有后端)

## 目录结构

```
.
├── index.html         # 首页:输入框 + 结果
├── browse.html        # 按主题浏览所有工具
├── about.html         # 项目说明 + 免责声明
├── styles.css
├── js/
│   ├── main.js
│   ├── matcher.js
│   ├── renderer.js
│   └── ai-mode.js
├── data/
│   ├── entries.json   # 知识库(PR 欢迎)
│   └── schema.md      # 贡献者看的数据结构说明
├── assets/favicon.svg
├── .github/workflows/deploy.yml
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

## 贡献新词条

非常欢迎。每一条"思想工具" = `entries.json` 里的一个对象。

- 数据结构见 [`data/schema.md`](data/schema.md)
- 贡献流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)
- 硬要求:每条原文引用 **≤ 80 字**,必须标明出处 URL,URL 必须指向公开可访问的权威资源(优先 marxists.org 中文库、cpc.people.com.cn、12371.cn)

## 免责声明

本项目是一个学习工具,目的是提取毛泽东著作中具有普适性的**分析方法**(矛盾分析、实事求是、实践认识论等),帮助读者思考当代个人困惑。项目**不提供政治立场、不替代专业心理 / 法律 / 医疗建议**。原文引用遵循合理使用原则,仅摘短句并标明出处,鼓励读者阅读完整原著。

## License

MIT — 见 [`LICENSE`](LICENSE)。欢迎 fork 做成你自己的思想工具箱(阳明心学版、斯多葛版、佛学版……)。

---

# v3 · 全集入库版(构建中)

> v1(上面的内容)是一个只读的静态站,基于手挑的 ~50 条词条。v3 在此之上增加一个**后端**与**完整的毛选四卷语料库 + RAG + 多轮对话 Agent**。v1 部分继续可用,v3 能力随开发进度逐步上线。

## 关于语料库

本项目的分析工具基于《毛泽东选集》四卷全集(约 159 篇,130 万字)。**本仓库不持有也不分发上游文本版权**。运行 `./setup.sh` 时,脚本会从以下公开数字化资源下载到你的本地 `corpus/` 目录(该目录已 gitignore):

- 主源:马克思主义文库 [marxists.org/chinese/maozedong](https://www.marxists.org/chinese/maozedong/)
- 备源:中文维基文库

前端展示每段引文 ≤ 80 字,并附上游链接,鼓励跳转读原文。若你在版权管辖地区有使用顾虑,请先自行评估合规性。

## 为什么我们不做"微调毛泽东大模型"

明确的边界:这个项目**不是**"用毛的口吻生成回复"。那样做有两个问题:(1) 幻觉不可控、引文无法溯源;(2) 把一个历史人物的语言风格商品化,越过了合理使用。v3 的做法是 **RAG**:原文只做检索与引用,不让模型拟写其口吻;"分析师"角色是一位熟读毛选的现代人,而非毛泽东本人。

## 规模说明

| 项目 | 规模 |
|---|---|
| 原文篇目 | ~159 篇 |
| 原文总字数 | ~130 万字 |
| 切块后 chunks | ~5000 条(预计) |
| 语料存储 | ~100MB(不 commit) |
| 向量索引 | ~60MB(不 commit) |
| 首次构建耗时 | 30-60 分钟(主要是抓取网络耗时) |

## 构建步骤

```bash
# 一次性,幂等,断点续传
./setup.sh
```

当前进度:**skeleton only**。`setup.sh` 会跑通 venv + 依赖安装 + manifest 验证;抓取/切块/向量化阶段在 Step 3-4 陆续点亮。

## v3 目录结构

```
backend/
├── main.py              # FastAPI 入口
├── rag.py               # 向量召回 + LLM 重排
├── agent.py             # 多轮对话 Agent
├── ingest/
│   ├── crawler.py       # marxists.org 抓取
│   ├── parser.py        # HTML → markdown
│   ├── chunker.py       # 切块
│   ├── embedder.py      # 向量化
│   ├── manifest.py      # 篇目清单读写
│   └── run.py           # 一键流水线
├── requirements.txt
└── .env.example
manifest/
└── maoxuan-index.json   # 159 篇元数据索引(committed)
corpus/                  # 运行时生成,大部分 gitignored
├── raw/vol1..vol4/      # 抓取后的 markdown
├── stories/             # 30+ 故事案例(committable)
├── chunks.jsonl         # 切块后的语料
└── index.faiss          # 向量索引
setup.sh
```

## 如何贡献 v3

1. **新增故事/现代案例** — PR 到 `corpus/stories/`
2. **改进清洗规则** — PR 到 `backend/ingest/parser.py`
3. **补充备用源** — PR 更新 `manifest/maoxuan-index.json` 中的 `url_backup`
4. **改进 prompt / agent 逻辑** — PR 到 `backend/`

