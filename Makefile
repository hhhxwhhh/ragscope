# Developer entry points. `make help` lists everything.
#
# Windows note: these targets assume bash (Git Bash / WSL), which is what the
# project's scripts already use.

PY := ./.venv/Scripts/python.exe
BACKEND := backend
FRONTEND := frontend

.DEFAULT_GOAL := help
.PHONY: help install dev-db dev-db-native dev-db-stop migrate seed seed-eval graph \
        test test-all lint fmt typecheck web-dev web-build up down logs eval-smoke mcp \
        eval-dataset eval-run eval-breakdown eval-ab

help: ## 列出所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 安装后端(含 dev)与前端依赖
	cd $(BACKEND) && python -m venv .venv && $(PY) -m pip install -e ".[dev]"
	cd $(FRONTEND) && npm ci

dev-db: ## 启动本地 PostgreSQL + Redis（Docker）
	docker compose up -d db redis

dev-db-native: ## 无 Docker 时用本机二进制启动 PG(5433) + Redis(6380)
	bash scripts/dev_stack.sh start

dev-db-stop: ## 停止本机原生 PG + Redis
	bash scripts/dev_stack.sh stop

migrate: ## 执行数据库迁移
	cd $(BACKEND) && $(PY) -m alembic upgrade head

seed: ## 灌入演示账号、知识库、文档与评测集
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/seed_demo.py

seed-eval: ## 只重建评测数据集（演示语料）
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/seed_eval.py

eval-dataset: ## 在真实语料上重建手工标注的评测集，需要传 KB=<uuid>
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/make_eval_dataset.py $(KB)

eval-run: ## 跑一轮检索评测，需要传 KB=<uuid>；可用 EXTRA 追加参数，如 EXTRA="--final-top-k 10"
	cd $(BACKEND) && PYTHONPATH=. HF_HUB_OFFLINE=1 $(PY) scripts/run_eval.py --kb $(KB) $(EXTRA)

eval-breakdown: ## 按用例构成拆分某一轮的指标，需要传 RUN=<uuid>
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/eval_breakdown.py $(RUN)

eval-ab: ## 基线 vs 放开 final_top_k 的对照（验证 Recall@k 曲线的天花板在哪）
	cd $(BACKEND) && PYTHONPATH=. HF_HUB_OFFLINE=1 $(PY) scripts/run_eval.py --kb $(KB)
	cd $(BACKEND) && PYTHONPATH=. HF_HUB_OFFLINE=1 $(PY) scripts/run_eval.py --kb $(KB) \
		--candidate-top-k 30 --final-top-k 10 --min-score 0.35

graph: ## 为所有知识库构建知识图谱（消耗 token）
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/build_graph.py --all

test: ## 单元测试（需要本地 PostgreSQL/Redis）
	cd $(BACKEND) && $(PY) -m pytest tests/unit -q

test-all: ## 全部测试
	cd $(BACKEND) && $(PY) -m pytest -q

lint: ## ruff 检查
	cd $(BACKEND) && $(PY) -m ruff check app scripts tests

fmt: ## ruff 自动修复
	cd $(BACKEND) && $(PY) -m ruff check app scripts tests --fix

typecheck: ## 前端类型检查
	cd $(FRONTEND) && npx vue-tsc --noEmit

web-dev: ## 前端开发服务器
	cd $(FRONTEND) && npm run dev

web-build: ## 前端生产构建
	cd $(FRONTEND) && npm run build

up: ## 生产编排（nginx + api + worker + prometheus + jaeger）
	docker compose -f docker-compose.prod.yml up --build -d

down: ## 停止生产编排
	docker compose -f docker-compose.prod.yml down

logs: ## 跟踪 worker 日志
	docker compose -f docker-compose.prod.yml logs -f worker

mcp: ## 以 stdio 方式启动 MCP server（需 RAGSCOPE_API_KEY）
	cd $(BACKEND) && $(PY) -m app.mcp.server
