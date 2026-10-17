# 从零手搓 RAG

这是我学习 RAG（Retrieval-Augmented Generation，检索增强生成）的练习项目。

目的不是做一个能上线的产品，而是不用 LangChain、LlamaIndex 这些框架，把 RAG 的每个环节自己写一遍。先跑通一个最小版本，再通过改参数、造问题、看失败案例，弄清楚每一步到底在影响什么。

语料是一套虚构的「星河科技」智能手表文档，中文。embedding 用 `BAAI/bge-small-zh-v1.5`，生成部分接 DeepSeek，全部在 Jupyter Notebook 里跑。

## 流程

```
离线索引
  data/*.md  ->  切块  ->  embedding  ->  内存里的向量矩阵

在线问答
  问题  ->  embedding  ->  和所有 chunk 算余弦相似度  ->  取 top-k
        ->  拼进 Prompt  ->  DeepSeek  ->  答案 + 来源编号
```

## 目前完成的

阶段 1 的最小 RAG 已经跑通，检索部分是手写的 numpy，没有用向量数据库：

- 读取 Markdown 文档，按固定长度 + overlap 切块
- 用 sentence-transformers 生成中文向量
- 用余弦相似度做 top-k 检索
- 把检索到的片段编号后拼进 Prompt，调用 DeepSeek 生成答案
- 回答里带 `[1]`、`[2]` 这样的来源编号

问「X1 手表的续航时间是多久？」时的实际输出：

> 根据资料，X1 手表的续航时间为：典型使用 14 天，重度使用 7 天。[1]
>
> [1] 02_智能手表X1产品手册.md #0 相似度 0.688

也测了语料里没有的问题（表带材质），模型回答「根据现有资料无法回答」，没有编。

## 目录

```
.
├── data/               # 5 篇虚构的产品文档
├── learn.ipynb         # 主 notebook，目前到阶段 1
├── requirements.txt
├── .env.example
├── LICENSE
└── .gitignore
```

## 运行

环境：Python 3.12，JupyterLab。有 GPU 会快一些，CPU 也能跑。

```bash
pip install -r requirements.txt
```

1. 复制 `.env.example` 为 `.env`，填入 DeepSeek API Key（在 https://platform.deepseek.com 申请）
2. 在项目根目录启动 JupyterLab：

```bash
jupyter lab
```

3. 打开 `learn.ipynb`，从上往下运行

## 换成自己的文档

把 Markdown 文件放进 `data/`，重新运行 notebook 即可。如果是 PDF、docx，需要先转成纯文本。

切块参数在 `split_text()` 里：

```python
def split_text(text, chunk_size=300, overlap=50):
```

中文文档可以先用 300 字左右、50 字重叠试起，再根据实际召回效果调整。问题越具体，块可以切得越小；需要跨段概括时，块就要大一些。

## 已知的局限

这一版只够用来理解流程，离实用还有距离：

- 按固定字符数切块，会把句子从中间截断，也可能把同一主题拆到两块
- 只用向量检索，遇到型号、编号这类关键词，效果可能不如 BM25
- 没有 rerank，top-k 里经常混进不相关的块
- 所有向量放在内存里，文档一多就要换向量库
- 没有评测集，改参数只能靠肉眼判断好坏

这些会在后面的阶段里逐个处理。

## 踩过的坑

**Hugging Face 下载失败。** 直连 huggingface.co 时 SSL 证书校验失败，换镜像解决。注意 `HF_ENDPOINT` 要在 import 之前设置，否则不生效：

```python
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
```

**Windows 读中文文件乱码。** `read_text()` 要显式写 `encoding="utf-8"`。

**Key 不要打印。** 一开始图方便写了 `print(key)`，输出会存进 `.ipynb` 文件。后来改成只打印是否读取成功。`.env` 已经加进 `.gitignore`。

## 后面要做的

1. 拿现有流程做参数实验：chunk_size、overlap、top_k，记录每次召回的结果
2. 加向量库，文档多了以后不用把向量全放内存
3. 加 BM25 混合检索和 rerank
4. 建一个小评测集，比较不同配置的 recall@k

都会接着写在 `learn.ipynb` 里。

## 参考

- [langchain-ai/rag-from-scratch](https://github.com/langchain-ai/rag-from-scratch)
- [NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques)
- [FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
