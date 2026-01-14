#!/usr/bin/env python3
import os
import pty
import time
import signal
import sys
import fcntl

# === Configuration ===
VIRTUAL_GPS_LINK = "/dev/gps0"

# Set Test Speeds (Modify here)
SPEED_HIGH = 20.0  # Phase A: Simulate Driving
SPEED_LOW  = 5.0   # Phase B: Simulate Low Speed / Lower Limit

def create_nmea_vtg(kph):
    """
    Helper function to generate a valid NMEA VTG string with Checksum.
    Formula: $GNVTG,,T,,M,{knots},N,{kph},K,D*checksum\r\n
    """
    # Convert km/h to knots (1 km/h = 0.539957 knots)
    knots = kph * 0.539957
    
    # Construct the payload (between $ and *)
    # Using .1f to match standard GPS precision (1 decimal place)
    payload = f"GNVTG,,T,,M,{knots:.1f},N,{kph:.1f},K,D"
    
    # Calculate Checksum (XOR of all characters in payload)
    checksum = 0
    for char in payload:
        checksum ^= ord(char)
        
    # Return formatted bytes
    nmea_string = f"${payload}*{checksum:02X}\r\n"
    return nmea_string.encode('utf-8')

def cleanup():
    """Cleanup: Remove the symlink."""
    if os.path.exists(VIRTUAL_GPS_LINK) and os.path.islink(VIRTUAL_GPS_LINK):
        try:
            os.unlink(VIRTUAL_GPS_LINK)
            print(f"\n[Clean] Removed {VIRTUAL_GPS_LINK}")
        except OSError as e:
            print(f"\n[Error] Failed to remove {VIRTUAL_GPS_LINK}: {e}")

def handle_signal(signum, frame):
    cleanup()
    sys.exit(0)

def main():
    # Check Root privileges (required for /dev operations)
    if os.geteuid() != 0:
        print("Error: This script must be run as ROOT (sudo).")
        sys.exit(1)

    # Register Ctrl+C signals for cleanup
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=== WinSet GPS Blanking Test Tool (Dynamic Speed) ===")

    # 1. Stop background Splitter service (to avoid conflict with /dev/gps0)
    print("1. Stopping real GPS Splitter service...")
    os.system("systemctl stop wmt_gps_splitter.service")
    time.sleep(1)

    # 2. Create virtual PTY
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    # 3. Create symlink /dev/gps0
    cleanup() # Remove old one first
    os.symlink(slave_name, VIRTUAL_GPS_LINK)
    os.chmod(VIRTUAL_GPS_LINK, 0o666)
    os.chmod(slave_name, 0o666)

    print(f"2. Virtual GPS Port Created: {VIRTUAL_GPS_LINK} -> {slave_name}")
    print("3. Simulation Started. Press Ctrl+C to stop.\n")
    print("   Please observe WinSet screen changes:")
    print(f"   - HIGH SPEED ({SPEED_HIGH} km/h): Screen should LOCK")
    print(f"   - LOW SPEED  ({SPEED_LOW} km/h):  Screen should UNLOCK (if threshold > {SPEED_LOW})")
    print("-" * 40)

    try:
        while True:
            # === Phase A: Simulate Driving ===
            nmea_high = create_nmea_vtg(SPEED_HIGH)
            print(f"[{time.strftime('%H:%M:%S')}] Status: DRIVING ({SPEED_HIGH} km/h) -> Sending: {nmea_high.strip().decode()}")
            
            for _ in range(10):
                os.write(master_fd, nmea_high)
                time.sleep(1) 

            # === Phase B: Simulate Low Speed (Threshold Test) ===
            nmea_low = create_nmea_vtg(SPEED_LOW)
            print(f"[{time.strftime('%H:%M:%S')}] Status: LOW SPEED ({SPEED_LOW} km/h) -> Sending: {nmea_low.strip().decode()}")
            
            for _ in range(10):
                os.write(master_fd, nmea_low)
                time.sleep(1)

    except OSError as e:
        print(f"Write error: {e}")
    finally:
        cleanup()
        print("4. Restarting real GPS Splitter service...")
        # Note: Ensure this service name matches your actual system
        os.system("systemctl restart wmt_gps_splitter.service")

if __name__ == "__main__":
    main()
