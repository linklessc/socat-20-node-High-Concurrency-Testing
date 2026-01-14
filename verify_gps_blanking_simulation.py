#!/usr/bin/env python3
import os
import pty
import time
import signal
import sys
import fcntl

# === Configuration ===
VIRTUAL_GPS_LINK = "/dev/gps0"
SERVICE_NAME = "wmt_gps_splitter.service"  # Name of the service in your system

# NMEA Format: $GNVTG,,T,,M,<knots>,N,<kph>,K,D*<checksum>
# WinSet only parses the value before 'K' (km/h). CheckSum is not strictly checked but included for standardization.

# 1. Stationary (0 km/h) -> Should UNLOCK (Below 5 km/h)
NMEA_0_KPH  = b"$GNVTG,,T,,M,0.0,N,0.0,K,D*26\r\n"

# 2. Medium Speed (10 km/h) -> Inside Hysteresis (5~20 km/h), should maintain current state
NMEA_10_KPH = b"$GNVTG,,T,,M,5.4,N,10.0,K,D*1C\r\n"

# 3. High Speed (25 km/h) -> Above 20 km/h, should LOCK
NMEA_25_KPH = b"$GNVTG,,T,,M,13.5,N,25.0,K,D*1F\r\n"

def cleanup():
    """Cleanup: Remove the symlink."""
    if os.path.exists(VIRTUAL_GPS_LINK) and os.path.islink(VIRTUAL_GPS_LINK):
        try:
            os.unlink(VIRTUAL_GPS_LINK)
        except OSError:
            pass
    print(f"\n[Clean] Removed {VIRTUAL_GPS_LINK}")

def handle_signal(signum, frame):
    cleanup()
    print(f"[System] Restoring real GPS service ({SERVICE_NAME})...")
    os.system(f"systemctl start {SERVICE_NAME}")
    sys.exit(0)

def main():
    # Check Root privileges
    if os.geteuid() != 0:
        print("Error: This script must be run as ROOT (sudo).")
        sys.exit(1)

    # Register Ctrl+C signals
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=== WinSet GPS Advanced Scenario Test ===")
    print(f"Target Port: {VIRTUAL_GPS_LINK}")
    print("---------------------------------------")

    # 1. Stop background service
    print(f"1. Stopping real GPS service ({SERVICE_NAME})...")
    os.system(f"systemctl stop {SERVICE_NAME}")
    time.sleep(1)

    # 2. Create virtual PTY
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    # 3. Create symlink
    cleanup()
    os.symlink(slave_name, VIRTUAL_GPS_LINK)
    os.chmod(VIRTUAL_GPS_LINK, 0o666)
    os.chmod(slave_name, 0o666)
    
    print(f"2. Virtual Port Ready: {slave_name} -> {VIRTUAL_GPS_LINK}")
    print("3. Starting Simulation sequence...\n")

    try:
        # ==========================================
        # Phase 1: Idle (Initial State)
        # ==========================================
        print(f"[{time.strftime('%H:%M:%S')}] [Phase 1] IDLE (0 km/h)")
        print("   -> Expect: Screen UNLOCKED")
        for _ in range(5):
            os.write(master_fd, NMEA_0_KPH)
            time.sleep(1)

        # ==========================================
        # Phase 2: Accelerate to 25 km/h (Trigger Lock)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 2] ACCELERATE (25 km/h)")
        print("   -> Expect: Screen LOCK (Threshold > 20)")
        for _ in range(5):
            os.write(master_fd, NMEA_25_KPH)
            time.sleep(1)

        # ==========================================
        # Phase 3: Slow down to 10 km/h (Hysteresis Test)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 3] SLOW DOWN (10 km/h)")
        print("   -> Expect: Screen STAYS LOCKED (Value is > 5)")
        # Although below 20, it is not below 5, so it should remain locked
        for _ in range(5):
            os.write(master_fd, NMEA_10_KPH)
            time.sleep(1)

        # ==========================================
        # Phase 4: Stop (0 km/h)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 4] STOP (0 km/h)")
        print("   -> Expect: Screen UNLOCK (Threshold < 5)")
        for _ in range(5):
            os.write(master_fd, NMEA_0_KPH)
            time.sleep(1)

        # ==========================================
        # Phase 5: Tunnel Test (Watchdog)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 5] ENTER TUNNEL (Signal Lost)")
        print("   -> (Simulating 25 km/h first to Lock screen...)")
        for _ in range(3):
            os.write(master_fd, NMEA_25_KPH)
            time.sleep(1)
        
        print(f"[{time.strftime('%H:%M:%S')}] -> SIGNAL LOST! (Stopping data transmission)")
        print("   -> Expect: Watchdog should UNLOCK screen after 5 seconds...")
        
        # Stop sending data for 8 seconds (Exceeds Watchdog 5s limit)
        for i in range(8):
            sys.stdout.write(f".")
            sys.stdout.flush()
            time.sleep(1)
        print("\n   -> Watchdog test complete. Screen should be UNLOCKED now.")

        # ==========================================
        # Phase 6: Exit Tunnel (Signal Restored)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 6] EXIT TUNNEL (Signal Restored 25 km/h)")
        print("   -> Expect: Screen LOCK immediately")
        for _ in range(5):
            os.write(master_fd, NMEA_25_KPH)
            time.sleep(1)

        print("\n=== Test Sequence Completed ===")
        print("Press Ctrl+C to exit and restore system.")
        while True:
            time.sleep(1)

    except OSError as e:
        print(f"Write error: {e}")
    finally:
        handle_signal(0, 0)

if __name__ == "__main__":
    main()
