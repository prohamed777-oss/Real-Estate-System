#!/bin/sh
# Production container entry: API + background worker in one container.
# If either process dies, the container exits and Railway's ON_FAILURE
# restart policy brings the whole container back.

python -m app.jobs.runner --interval 5 &
RUNNER=$!

uvicorn app.main:app --host 0.0.0.0 --port $PORT &
API=$!

wait -n $RUNNER $API
echo "a core process exited — shutting down for restart" >&2
exit 1
