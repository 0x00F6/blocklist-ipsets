.PHONY: help check test build generate archive fmt
DATA ?= data
OUTPUT ?= dist/firehol-blocklist-ipsets.mmdb
help:
	@echo 'make check | test | build | generate DATA=... OUTPUT=... | archive DATA=... OUTPUT=... | fmt'
check:
	cargo fmt --check
	cargo clippy --locked --all-targets -- -D warnings
	cargo test --locked
	python3 -m unittest discover -s tests -p 'test_*.py' -v
test:
	cargo test --locked
	python3 -m unittest discover -s tests -p 'test_*.py' -v
build:
	cargo build --release --locked
generate: build
	target/release/firehol-mmdb "$(DATA)" "$(OUTPUT)"
archive:
	python3 scripts/archive.py "$(OUTPUT)" "$(DATA)"
fmt:
	cargo fmt
