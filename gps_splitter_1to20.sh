#!/bin/bash

# === Configuration ===
CONFIG_FILE="/bin/wmt_winset/config.ini"
SOURCE_DEV="/dev/ttyACM0"   # Physical GPS Device
START_ID=1                  # Start ID /dev/gps1
END_ID=20                   # Default End ID /dev/gps20 (Fallback)

# === 1. Read Configuration ===
# Check if the configuration file exists
if [ -f "$CONFIG_FILE" ]; then
    # Fetch the line starting with "gps_port", cut by "=", and remove ALL whitespace/newlines
    READ_PORT=$(grep "^gps_port" "$CONFIG_FILE" | head -n 1 | cut -d'=' -f2 | tr -d ' \r\n')
    
    # Check if the result is a valid integer number
    if [[ "$READ_PORT" =~ ^[0-9]+$ ]]; then
        END_ID=$READ_PORT
        echo "Config loaded: gps_port = $END_ID"
    else
        echo "Error: gps_port not found or invalid in $CONFIG_FILE. Defaulting to 20."
        END_ID=20
    fi
else
    echo "Warning: Config file not found ($CONFIG_FILE). Defaulting to 20."
    END_ID=20
fi

echo "=== Starting creation of virtual nodes /dev/gps$START_ID to /dev/gps$END_ID ==="

# === 2. Generate Command String ===
# Base command: Read from source and split via tee
CMD_STR="cat $SOURCE_DEV | tee"

# Loop: Generate gps1 to gps(END_ID)
for (( i=START_ID; i<=END_ID; i++ ))
do
    # Remove old links first (to avoid conflict errors)
    rm -f "/dev/gps$i"

    # Append socat parameters using Process Substitution
    CMD_STR="$CMD_STR >(socat -u - PTY,link=/dev/gps$i,raw,echo=0,mode=666)"
    
    echo " -> Preparing to create: /dev/gps$i"
done

# === 3. End of Command ===
# Discard tee's standard output to prevent screen flooding
CMD_STR="$CMD_STR > /dev/null"

echo "=== Running Socat Splitting Service... ==="
# === 4. Execute ===
eval "$CMD_STR"