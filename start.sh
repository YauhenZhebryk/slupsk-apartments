#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

PORT=5050

case "$1" in
  start)
    echo "Starting Słupsk Apartment Tracker service..."
    systemctl --user daemon-reload
    systemctl --user start slupsk-apartments.service
    sleep 1
    systemctl --user status slupsk-apartments.service --no-pager
    echo ""
    echo "Web Dashboard is available at: http://localhost:$PORT"
    ;;
  stop)
    echo "Stopping Słupsk Apartment Tracker service..."
    systemctl --user stop slupsk-apartments.service
    echo "Stopped."
    ;;
  status)
    systemctl --user status slupsk-apartments.service --no-pager
    ;;
  restart)
    echo "Restarting service..."
    systemctl --user restart slupsk-apartments.service
    sleep 1
    systemctl --user status slupsk-apartments.service --no-pager
    ;;
  logs)
    journalctl --user -u slupsk-apartments.service -f
    ;;
  *)
    echo "Usage: ./start.sh {start|stop|restart|status|logs}"
    echo "Or run manually: python3 app.py"
    ;;
esac
