# 手搓 RAG 学习路线

> 目标：不用 LangChain，从零实现一个 RAG 系统，并真正理解每一步在做什么。
> 方式：一段一段来，自己在 Jupyter Notebook 里输入代码并观察结果。

## 一句话理解 RAG

RAG = Retrieval-Augmented Generation（检索增强生成）。

大模型本身不知道你的私有资料，也会胡编。RAG 的做法是：

1. 先把你自己的资料切成小段，存成一个「可以按语义搜索」的库；
2. 用户提问时，先从库里找出最相关的几段；
3. 把这几段原文和问题一起塞给大模型，让它「看着资料回答」。

所以 RAG 有两个阶段：

```
离线索引（提前做一次）
文档 → 切块 → 向量化 → 存入向量库

在线问答（每次提问）
问题 → 向量化 → 检索 top-k 片段 → 拼 Prompt → LLM → 答案
```

## 阶段划分

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| 0. 准备 | 理解 RAG 全貌，跑通 DeepSeek API | 环境检查 |
| 1. 最小 RAG | 读文件、切块、embedding、余弦相似度检索、拼 Prompt 调 LLM，全部手写 | `01_minimal_rag.ipynb` |
| 2. 拆解实验 | 改 chunk_size / overlap / top_k，观察召回变化，认识失败案例 | 实验记录 |
| 3. 工程改进 | 向量库（Chroma）、BM25 混合检索、rerank、引用溯源 | `02_improved_rag.ipynb` |
| 4. 评估 | 建小评测集，算 recall@k、MRR，比较不同配置 | `03_evaluate.ipynb` |
| 5. 进阶 | FastAPI 服务、query rewriting、HyDE、parent-document、GraphRAG | 按兴趣选做 |

## 参考的开源项目

- [langchain-ai/rag-from-scratch](https://github.com/langchain-ai/rag-from-scratch) —— 最适合入门，一整套从零讲 RAG 的 notebook
- [NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques) —— 30+ 种 RAG 技术的可运行 notebook，当字典查
- [openai/openai-cookbook](https://github.com/openai/openai-cookbook) —— 官方示例，看工程写法
- [FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) —— 中文 embedding / reranker（BGE）官方库
- [chatchat-space/Langchain-Chatchat](https://github.com/chatchat-space/Langchain-Chatchat) —— 中文 RAG 应用，看完整项目长什么样
- [infiniflow/ragflow](https://github.com/infiniflow/ragflow) —— 工业级 RAG 引擎，进阶参考

## 约定

- 示例语料放在 `data/`，可以随时换成你自己的文档。
- API Key 放在 `.env`，不要写进 notebook，也不要提交到 git。
- 每个阶段先跑通、再改参数、最后看失败案例。
