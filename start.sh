#!/bin/bash
echo "Starting VoiceShield AI services..."

# Start Python Sidecar
cd backend
../.venv/bin/python3 -m app.sidecar &
SIDECAR_PID=$!
cd ..

# Start Go Gateway
cd gateway
go run ./cmd/voxguard &
GATEWAY_PID=$!
cd ..

# Start Node Frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo "All services started! The dashboard is available at http://localhost:5173"
echo "Press Ctrl+C to stop."

# Trap exit signals to kill background processes
trap "echo 'Stopping services...'; kill $SIDECAR_PID $GATEWAY_PID $FRONTEND_PID 2>/dev/null" EXIT INT TERM

# Wait indefinitely
wait
