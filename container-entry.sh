#!/bin/sh
set -eu
# Xvfb provides a Linux display for WinForms; viewing happens in the browser.
exec xvfb-run -a -s "-screen 0 1280x720x24" python /app/jev.py "$@"
