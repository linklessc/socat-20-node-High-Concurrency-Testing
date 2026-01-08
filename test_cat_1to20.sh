#!/bin/bash

START_ID=1
END_ID=20

# === Define Ctrl+C Behavior (Important!) ===
# When SIGINT (Ctrl+C) is received, kill all child processes of the current shell
trap 'echo " Received interrupt signal, shutting down all cat processes..."; kill $(jobs -p); exit' SIGINT

echo "=== Starting simultaneous read of /dev/gps$START_ID to /dev/gps$END_ID ==="
echo "Note: The screen may be flooded with data (Press Ctrl+C to stop immediately)"
sleep 2

for (( i=START_ID; i<=END_ID; i++ ))
do
    # Check if the node exists
    if [ ! -e "/dev/gps$i" ]; then
        echo "Warning: /dev/gps$i does not exist, skipping."
        continue
    fi

    # === Core Command ===
    # Start cat in the background (&)
    # If you only want to test load without seeing content, change the next line to: cat "/dev/gps$i" > /dev/null &
    cat "/dev/gps$i" & 
    
    echo " -> Started listening: /dev/gps$i (PID: $!)"
done

echo "=== All listening processes started. Please check htop or data flow ==="
echo "=== Press [Ctrl+C] to end test ==="

# Keep the script waiting here until the user presses Ctrl+C
wait