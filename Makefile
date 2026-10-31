# Developer entry points. `make help` lists everything.
#
# Windows note: these targets assume bash (Git Bash / WSL), which is what the
# project's scripts already use.

PY := ./.venv/Scripts/python.exe
BACKEND := backend
FRONTEND := frontend

.DEFAULT_GOAL := help
.PHONY: help install dev-db migrate seed seed-eval graph test test-all lint fmt typecheck \
        web-dev web-build up down logs eval-smoke mcp

help: ## 列出所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 安装后端(含 dev)与前端依赖
	cd $(BACKEND) && python -m venv .venv && $(PY) -m pip install -e ".[dev]"
	cd $(FRONTEND) && npm ci

dev-db: ## 启动本地 PostgreSQL + Redis
	docker compose up -d db redis

migrate: ## 执行数据库迁移
	cd $(BACKEND) && $(PY) -m alembic upgrade head

seed: ## 灌入演示账号、知识库、文档与评测集
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/seed_demo.py

seed-eval: ## 只重建评测数据集
	cd $(BACKEND) && PYTHONPATH=. $(PY) scripts/seed_eval.py

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
