#!/bin/bash

# === Configuration ===
SOURCE_DEV="/dev/ttyACM0"   # Physical GPS Device
START_ID=1                  # Start ID /dev/gps1
END_ID=20                   # End ID /dev/gps20

echo "=== Starting creation of virtual nodes /dev/gps$START_ID to /dev/gps$END_ID ==="

# === Generate Command String ===
# Base command: Read from source and split via tee
CMD_STR="cat $SOURCE_DEV | tee"

# Loop: Generate gps1 to gps20
for (( i=START_ID; i<=END_ID; i++ ))
do
    # 1. Remove old links first (to avoid conflict errors)
    rm -f "/dev/gps$i"

    # 2. Append socat parameters
    # Use process substitution >(...) to pipe tee output into socat
    CMD_STR="$CMD_STR >(socat -u - PTY,link=/dev/gps$i,raw,echo=0,mode=666)"
    
    echo " -> Preparing to create: /dev/gps$i"
done

# === End of Command ===
# Finally, discard tee's standard output to prevent screen flooding
CMD_STR="$CMD_STR > /dev/null"

echo "=== Running Socat Splitting Service... ==="
echo "The command string is long, please wait..."

# === Execute ===
# Note: This command will run continuously until you press Ctrl+C
eval "$CMD_STR"