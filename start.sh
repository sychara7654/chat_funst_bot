#!/bin/bash
set -e

# Юзербот-спаммер (фон)
if [ -n "$SPAM_SESSION" ]; then
    python userbot_spammer.py &
    echo "userbot_spammer started"
fi

# Бизнес-бот — только если токен задан
if [ -n "$TOKEN2" ]; then
    exec python bot.py
else
    echo "TOKEN2 not set — bot.py skipped, keeping container alive"
    tail -f /dev/null
fi
