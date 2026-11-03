#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../../../.."
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
mkdir -p work
build=$(mktemp -d "$PWD/work/handsy-offline.XXXXXX")
trap 'rm -rf "$build"' EXIT
xcrun --sdk macosx swiftc -D HANDSY_OFFLINE_TEST -module-cache-path "$build/modules" \
  legacy/tools/handsy-runner/HandsyRunnerUITests/Protocol.swift \
  legacy/tools/handsy-runner/HandsyRunnerUITests/XCTestAutomation.swift \
  legacy/tools/handsy-runner/Tests/XCTestStub.swift legacy/tools/handsy-runner/Tests/main.swift -o "$build/tests"
if [[ $# -gt 0 ]]; then
  "$build/tests" "$@"
else
  "$build/tests" --self-test
  "$build/tests" --snapshot-test
  "$build/tests" --find-test
  "$build/tests" --budget-test
fi
