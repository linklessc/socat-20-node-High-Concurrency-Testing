#!/bin/bash

# === Configuration ===
CONFIG_FILE="/bin/wmt_winset/config.ini"
SOURCE_DEV="/dev/ttyACM0"   # Physical GPS Device
START_ID=0                  # Start ID /dev/gps0

# Initialize logic flag (Default is: DO NOT RUN)
RUN_SPLITTER=false
END_ID=-1

# === 1. Read Configuration ===
# Check if the configuration file exists
if [ -f "$CONFIG_FILE" ]; then
    # Fetch the line, cut by "=", remove whitespace/newlines
    # tr '[:upper:]' '[:lower:]' ensures we can handle "False", "FALSE", or "false"
    RAW_VAL=$(grep "^gps_port" "$CONFIG_FILE" | head -n 1 | cut -d'=' -f2 | tr -d ' \r\n')
    LOWER_VAL=$(echo "$RAW_VAL" | tr '[:upper:]' '[:lower:]')
    
    # === Logic: Check value ===
    if [[ "$LOWER_VAL" == "false" ]]; then
        echo "Config loaded: gps_port = false. GPS Splitter disabled."
        RUN_SPLITTER=false

    elif [[ "$RAW_VAL" == "0" ]]; then
        echo "Config loaded: gps_port = 0. GPS Splitter disabled."
        RUN_SPLITTER=false

    elif [[ "$RAW_VAL" =~ ^[0-9]+$ ]]; then
        # It is a valid number and greater than 0 (since we handled 0 above)
        END_ID=$RAW_VAL
        RUN_SPLITTER=true
        echo "Config loaded: gps_port = $END_ID. Enabling Splitter."
    else
        # Invalid content (e.g., text that isn't 'false' or a number)
        echo "Error: Invalid value in config ('$RAW_VAL'). Defaulting to DISABLED."
        RUN_SPLITTER=false
    fi
else
    echo "Warning: Config file not found ($CONFIG_FILE). Defaulting to DISABLED."
    RUN_SPLITTER=false
fi

# === 2. Stop if disabled ===
if [ "$RUN_SPLITTER" = false ]; then
    echo "Exiting without creating virtual ports."
    exit 0
fi

# ==========================================
# Only reach here if gps_port > 0
# ==========================================

echo "=== Starting creation of virtual nodes /dev/gps$START_ID to /dev/gps$END_ID ==="

# === 3. Generate Command String ===
CMD_STR="cat $SOURCE_DEV | tee"

# Loop: Generate gps(START_ID) to gps(END_ID)
for (( i=START_ID; i<END_ID; i++ ))
do
    # Remove old links first
    rm -f "/dev/gps$i"

    # Append socat parameters
    CMD_STR="$CMD_STR >(socat -u - PTY,link=/dev/gps$i,raw,echo=0,mode=666)"
    
    echo " -> Preparing to create: /dev/gps$i"
done

# === 4. End of Command ===
CMD_STR="$CMD_STR > /dev/null"

echo "=== Running Socat Splitting Service... ==="
# === 5. Execute ===
eval "$CMD_STR"