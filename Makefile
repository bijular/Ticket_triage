.PHONY: data test verify plan serve ui-install ui-build ui ui-dev
data:    ; python3 -m triage_eval generate
verify:  ; python3 -m triage_eval verify
test:    ; python3 -m unittest discover -s tests
plan:    ; python3 -m triage_eval plan
serve:   ; (python3 -m triage_eval serve-tickets &) ; python3 -m triage_eval serve-data

# Operator console (FastAPI + React). The kit itself stays stdlib-only.
ui-install: ; python3 -m venv .venv && .venv/bin/pip install -r requirements-web.txt && cd webapp && npm ci
ui-build:   ; cd webapp && npm run build
ui:         ; .venv/bin/python -m triage_eval serve-ui
ui-dev:     ; (.venv/bin/python -m triage_eval serve-ui &) ; cd webapp && npm run dev
