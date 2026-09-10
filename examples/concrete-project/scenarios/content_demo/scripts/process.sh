#!/bin/sh
set -eu
umask 077
tr '[:lower:]' '[:upper:]' < "$1" > "$2"
