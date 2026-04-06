#!/bin/zsh
cd "$(dirname "$0")"
export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
exec python3 scrape.py
