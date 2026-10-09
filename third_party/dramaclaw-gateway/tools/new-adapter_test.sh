#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GENERATOR="$ROOT_DIR/tools/new-adapter.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/dramaclaw-new-adapter-test.XXXXXX")"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf 'new-adapter test: %s\n' "$1" >&2
  exit 1
}

assert_file() {
  [[ -f "$1" ]] || fail "expected file: $1"
}

assert_rejected_without_output() {
  local output_root="$1"
  shift
  if OUTPUT_ROOT="$output_root" "$GENERATOR" "$@" >/dev/null 2>&1; then
    fail "expected generator rejection for PROVIDER=${PROVIDER:-} TYPE=${TYPE:-} MODE=${MODE:-}"
  fi
  [[ ! -e "$output_root" ]] || fail "rejected input created output: $output_root"
}

go_keywords=(
  break default func interface select case defer go map struct chan else goto
  package switch const fallthrough if range type continue for import return var
)

for keyword in "${go_keywords[@]}"; do
  PROVIDER="$keyword" TYPE=9001 MODE=sync CAPABILITIES=image \
    assert_rejected_without_output "$TEST_ROOT/keyword-$keyword"
done

PROVIDER=occupied_type TYPE=61 MODE=task CAPABILITIES=video \
  assert_rejected_without_output "$TEST_ROOT/occupied-type"

gofmt_tmp="$TEST_ROOT/gofmt-tmp"
mkdir -p "$gofmt_tmp"
if TMPDIR="$gofmt_tmp" OUTPUT_ROOT="$TEST_ROOT/gofmt-output" GOFMT_BIN=false \
  PROVIDER=gofmt_failure TYPE=9001 MODE=sync CAPABILITIES=image \
  "$GENERATOR" >/dev/null 2>&1; then
  fail "generator succeeded when gofmt failed"
fi
[[ ! -e "$TEST_ROOT/gofmt-output" ]] || fail "gofmt failure created target output"
[[ -z "$(find "$gofmt_tmp" -mindepth 1 -print -quit)" ]] || fail "gofmt failure left staged files"

task_root="$TEST_ROOT/task"
OUTPUT_ROOT="$task_root" PROVIDER=scaffold_task TYPE=9001 MODE=task CAPABILITIES=video,audio \
  "$GENERATOR" >/dev/null
assert_file "$task_root/relay/channel/task/scaffold_task/adaptor.go"
assert_file "$task_root/relay/channel/task/scaffold_task/adaptor_test.go"
assert_file "$task_root/relay/channel/task/scaffold_task/constants.go"
assert_file "$task_root/docs/providers/scaffold_task.md"
assert_file "$task_root/docs/providers/en/scaffold_task.md"

sync_root="$TEST_ROOT/sync"
OUTPUT_ROOT="$sync_root" PROVIDER=scaffold_sync TYPE=9002 MODE=sync NAME="Scaffold Sync" CAPABILITIES=image,vision \
  "$GENERATOR" >/dev/null
assert_file "$sync_root/relay/channel/scaffold_sync/adaptor.go"
assert_file "$sync_root/relay/channel/scaffold_sync/adaptor_test.go"
assert_file "$sync_root/relay/channel/scaffold_sync/constants.go"
assert_file "$sync_root/docs/providers/scaffold_sync.md"
assert_file "$sync_root/docs/providers/en/scaffold_sync.md"

if OUTPUT_ROOT="$task_root" PROVIDER=scaffold_task TYPE=9003 MODE=task CAPABILITIES=video \
  "$GENERATOR" >/dev/null 2>&1; then
  fail "duplicate provider was accepted"
fi

if [[ -n "$(gofmt -d \
  "$task_root/relay/channel/task/scaffold_task"/*.go \
  "$sync_root/relay/channel/scaffold_sync"/*.go)" ]]; then
  fail "generated Go files are not formatted"
fi

printf 'new-adapter tests: OK\n'
