#!/usr/bin/env python3
import os
import pty
import time
import signal
import sys
import fcntl

# === Configuration ===
VIRTUAL_GPS_LINK = "/dev/gps0"

# Simulated high-speed data (20 km/h, > 5 km/h triggers blanking)
# Note: 20 km/h is approx 10.8 knots. Checksum updated to *1D.
NMEA_SPEED_HIGH = b"$GNVTG,,T,,M,10.8,N,20.0,K,D*1D\r\n"

# Simulated stationary data (0 km/h, < 3 km/h releases blanking)
NMEA_SPEED_ZERO = b"$GNVTG,,T,,M,0.0,N,0.0,K,D*26\r\n"

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

    print("=== WinSet GPS Blanking Test Tool ===")

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
    print("   - DRIVING: Screen should **BLACK OUT / LOCK**")
    print("   - STOPPED: Screen should **RESTORE / UNLOCK**")
    print("-" * 40)

    try:
        while True:
            # === Phase A: Simulate Driving (10 sec) ===
            print(f"[{time.strftime('%H:%M:%S')}] Status: DRIVING (20.0 km/h) -> Screen should LOCK")
            for _ in range(10):
                os.write(master_fd, NMEA_SPEED_HIGH)
                time.sleep(1) # Send GPS data once per second

            # === Phase B: Simulate Stopping (10 sec) ===
            print(f"[{time.strftime('%H:%M:%S')}] Status: STOPPED (0.0 km/h) -> Screen should UNLOCK")
            for _ in range(10):
                os.write(master_fd, NMEA_SPEED_ZERO)
                time.sleep(1)

    except OSError as e:
        print(f"Write error: {e}")
    finally:
        cleanup()
        print("4. Restarting real GPS Splitter service...")
        # Note: Original script used 'wmt_gps_splitter.service' for stop and 'gps_splitter' for start.
        # Ensure these match your actual system service names.
        os.system("systemctl restart wmt_gps_splitter.service")

if __name__ == "__main__":
    main()