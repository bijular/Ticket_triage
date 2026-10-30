.PHONY: data test verify plan serve
data:    ; python3 -m triage_eval generate
verify:  ; python3 -m triage_eval verify
test:    ; python3 -m unittest discover -s tests
plan:    ; python3 -m triage_eval plan
serve:   ; (python3 -m triage_eval serve-tickets &) ; python3 -m triage_eval serve-data
