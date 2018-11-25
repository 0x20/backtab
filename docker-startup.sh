#!/bin/bash

set -euo pipefail

if ! [[ -z "${TEST_MODE:+test}" ]]; then
  if ! [[ -d /srv/backtab/tab-data.git ]]; then
    git clone --bare "${TAB_DATA_REPO}" /srv/backtab/tab-data.git
  fi
  TAB_DATA_REPO=/srv/backtab/tab-data.git
fi

if ! [[ -d /srv/backtab/tab-data ]]; then
  git clone "${TAB_DATA_REPO}" /srv/backtab/tab-data
fi

exec /usr/local/bin/backtab-server -c /etc/backtab.yml
