SHELL := /bin/bash
PYTHON ?= python3.11
VENV := backend/.venv
BIN := $(VENV)/bin

-include .env
BACKEND_PORT ?= 8000
export BACKEND_PORT

.PHONY: setup backend-setup frontend-setup dev backend frontend test app install-app clean

setup: backend-setup frontend-setup
	@test -f .env || (cp .env.example .env && echo "已生成 .env，请填写 BAIDU_APP_KEY / BAIDU_SECRET_KEY / MUSIC_ROOT")

backend-setup:
	@$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11+"' 
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e "backend[dev]"

frontend-setup:
	cd frontend && npm install

dev:
	@test -d $(VENV) || (echo "请先运行 make setup" && exit 1)
	@test -d frontend/node_modules || (echo "请先运行 make setup" && exit 1)
	@trap 'trap - INT TERM EXIT; kill 0 2>/dev/null' INT TERM EXIT; \
	$(MAKE) --no-print-directory backend & \
	$(MAKE) --no-print-directory frontend & \
	wait

backend:
	$(BIN)/python -m app.serve

frontend:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/pytest -q

APP := packaging/dist/唱片目录.app

app:
	@test -d $(VENV) || (echo "请先运行 make setup" && exit 1)
	$(BIN)/pip install -q -e "backend[desktop]"
	cd frontend && npm run build
	$(BIN)/python packaging/make_icon.py
	$(BIN)/pyinstaller --noconfirm --clean --log-level WARN \
		--distpath packaging/dist --workpath packaging/build/pyinstaller packaging/app.spec
	@echo "已生成 $(APP)"

install-app: app
	$(BIN)/python packaging/setup_config.py
	rm -rf "/Applications/唱片目录.app"
	cp -R "$(APP)" /Applications/
	@echo "已安装到 /Applications/唱片目录.app"

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist
