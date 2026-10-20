#!/bin/sh
set -e
cd "$(git rev-parse --show-toplevel)"
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
echo "pre-commit secret scan installed"
