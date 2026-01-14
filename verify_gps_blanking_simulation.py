#!/usr/bin/env python3
# Shebang line: Specifies that this script should be executed using python3.

import os
# Import the 'os' module for Operating System interactions (file system, permissions, system commands).

import pty
# Import the 'pty' module for creating pseudo-terminal pairs (master/slave), essential for virtualizing serial ports.

import time
# Import the 'time' module to handle delays (sleep) and time formatting.

import signal
# Import the 'signal' module to capture system signals like Ctrl+C (SIGINT) for graceful shutdowns.

import sys
# Import the 'sys' module for system-specific parameters and functions (like exit codes).

import fcntl
# Import the 'fcntl' module for file control and I/O control (ioctl), though not heavily used in this specific logic.

# === Configuration ===
VIRTUAL_GPS_LINK = "/dev/gps0"
# Define the path for the virtual GPS symbolic link. The target application (WinSet) will read from this path.

SERVICE_NAME = "wmt_gps_splitter.service"  # Name of the service in your system
# Define the name of the real system service that normally manages the GPS. We need to stop it to avoid conflicts.

# NMEA Format: $GNVTG,,T,,M,<knots>,N,<kph>,K,D*<checksum>
# Comment explaining the NMEA sentence format expected by the parser.
# WinSet only parses the value before 'K' (km/h). CheckSum is not strictly checked but included for standardization.

# 1. Stationary (0 km/h) -> Should UNLOCK (Below 5 km/h)
# Define a bytes object representing a GPS NMEA sentence for 0 km/h speed.
NMEA_0_KPH  = b"$GNVTG,,T,,M,0.0,N,0.0,K,D*26\r\n"

# 2. Medium Speed (10 km/h) -> Inside Hysteresis (5~20 km/h), should maintain current state
# Define a bytes object representing a GPS NMEA sentence for 10 km/h.
# This tests the "hysteresis" logic: if it was locked, it stays locked; if unlocked, it stays unlocked (usually).
NMEA_10_KPH = b"$GNVTG,,T,,M,5.4,N,10.0,K,D*1C\r\n"

# 3. High Speed (25 km/h) -> Above 20 km/h, should LOCK
# Define a bytes object representing a GPS NMEA sentence for 25 km/h.
# This is above the lock threshold (typically 20 km/h), so it should trigger a screen lock.
NMEA_25_KPH = b"$GNVTG,,T,,M,13.5,N,25.0,K,D*1F\r\n"

def cleanup():
    """Cleanup: Remove the symlink."""
    # Define a function to clean up the environment (remove the fake port) when the script ends.

    if os.path.exists(VIRTUAL_GPS_LINK) and os.path.islink(VIRTUAL_GPS_LINK):
        # Check if the file exists AND if it is actually a symbolic link (safety check).

        try:
            os.unlink(VIRTUAL_GPS_LINK)
            # Try to delete (unlink) the symbolic link.
        except OSError:
            pass
            # If an error occurs (e.g., file already gone), ignore it.

    print(f"\n[Clean] Removed {VIRTUAL_GPS_LINK}")
    # Print a message confirming the cleanup.

def handle_signal(signum, frame):
    # Define a signal handler function to run when Ctrl+C or termination signals are received.

    cleanup()
    # Call the cleanup function defined above to remove the virtual port.

    print(f"[System] Restoring real GPS service ({SERVICE_NAME})...")
    # Inform the user that the original system service is being restarted.

    os.system(f"systemctl start {SERVICE_NAME}")
    # Execute the shell command to restart the real GPS background service.

    sys.exit(0)
    # Exit the script with a success status code (0).

def main():
    # Define the main execution function.

    # Check Root privileges
    if os.geteuid() != 0:
        # Check if the effective user ID is 0 (root).

        print("Error: This script must be run as ROOT (sudo).")
        # If not root, print an error message.

        sys.exit(1)
        # Exit with an error status code (1).

    # Register Ctrl+C signals
    signal.signal(signal.SIGINT, handle_signal)
    # Register the handle_signal function to catch SIGINT (Ctrl+C).

    signal.signal(signal.SIGTERM, handle_signal)
    # Register the handle_signal function to catch SIGTERM (termination signal).

    print("=== WinSet GPS Advanced Scenario Test ===")
    print(f"Target Port: {VIRTUAL_GPS_LINK}")
    print("---------------------------------------")
    # Print the banner and configuration info.

    # 1. Stop background service
    print(f"1. Stopping real GPS service ({SERVICE_NAME})...")
    # Inform the user that we are stopping the conflicting service.

    os.system(f"systemctl stop {SERVICE_NAME}")
    # Execute the command to stop the real GPS service.

    time.sleep(1)
    # Wait for 1 second to ensure the service has fully stopped and released the port.

    # 2. Create virtual PTY
    master_fd, slave_fd = pty.openpty()
    # Create a new pseudo-terminal pair. 'master_fd' is for writing data, 'slave_fd' is for the app to read.

    slave_name = os.ttyname(slave_fd)
    # Get the actual file system path of the slave terminal (e.g., /dev/pts/3).

    # 3. Create symlink
    cleanup()
    # Ensure any old link is removed before creating a new one.

    os.symlink(slave_name, VIRTUAL_GPS_LINK)
    # Create a symbolic link from the slave PTY path to the standard GPS path (/dev/gps0).

    os.chmod(VIRTUAL_GPS_LINK, 0o666)
    # Set permissions on the link to be readable/writable by everyone.

    os.chmod(slave_name, 0o666)
    # Set permissions on the actual slave device to be readable/writable by everyone.
    
    print(f"2. Virtual Port Ready: {slave_name} -> {VIRTUAL_GPS_LINK}")
    # Print the mapping of the virtual port.

    print("3. Starting Simulation sequence...\n")
    # Indicate the start of the test loop.

    try:
        # ==========================================
        # Phase 1: Idle (Initial State)
        # ==========================================
        print(f"[{time.strftime('%H:%M:%S')}] [Phase 1] IDLE (0 km/h)")
        # Print timestamp and phase description: Idle state.

        print("   -> Expect: Screen UNLOCKED")
        # Print expected outcome: The screen should be usable.

        for _ in range(5):
            # Loop 5 times (send data for 5 seconds).

            os.write(master_fd, NMEA_0_KPH)
            # Write the 0 km/h NMEA sentence to the master PTY (WinSet reads this).

            time.sleep(1)
            # Wait for 1 second before sending the next packet.

        # ==========================================
        # Phase 2: Accelerate to 25 km/h (Trigger Lock)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 2] ACCELERATE (25 km/h)")
        # Print timestamp and phase description: Acceleration.

        print("   -> Expect: Screen LOCK (Threshold > 20)")
        # Print expected outcome: Speed > 20 km/h should lock the screen.

        for _ in range(5):
            # Loop 5 times.

            os.write(master_fd, NMEA_25_KPH)
            # Write the 25 km/h NMEA sentence.

            time.sleep(1)
            # Wait 1 second.

        # ==========================================
        # Phase 3: Slow down to 10 km/h (Hysteresis Test)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 3] SLOW DOWN (10 km/h)")
        # Print timestamp and phase description: Deceleration.

        print("   -> Expect: Screen STAYS LOCKED (Value is > 5)")
        # Print expected outcome: Even though speed dropped, it's not below the unlock threshold (5 km/h), so it stays locked.

        # Although below 20, it is not below 5, so it should remain locked
        for _ in range(5):
            # Loop 5 times.

            os.write(master_fd, NMEA_10_KPH)
            # Write the 10 km/h NMEA sentence.

            time.sleep(1)
            # Wait 1 second.

        # ==========================================
        # Phase 4: Stop (0 km/h)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 4] STOP (0 km/h)")
        # Print timestamp and phase description: Full stop.

        print("   -> Expect: Screen UNLOCK (Threshold < 5)")
        # Print expected outcome: Speed dropped below 5 km/h, screen should unlock.

        for _ in range(5):
            # Loop 5 times.

            os.write(master_fd, NMEA_0_KPH)
            # Write the 0 km/h NMEA sentence.

            time.sleep(1)
            # Wait 1 second.

        # ==========================================
        # Phase 5: Tunnel Test (Watchdog)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 5] ENTER TUNNEL (Signal Lost)")
        # Print timestamp and phase description: Simulating entering a tunnel (signal loss).

        print("   -> (Simulating 25 km/h first to Lock screen...)")
        # Explanation: We need to lock the screen first to prove the watchdog unlocks it.

        for _ in range(3):
            # Loop 3 times to establish high speed.

            os.write(master_fd, NMEA_25_KPH)
            # Write 25 km/h data to lock the screen.

            time.sleep(1)
            # Wait 1 second.
        
        print(f"[{time.strftime('%H:%M:%S')}] -> SIGNAL LOST! (Stopping data transmission)")
        # Indicate that we are now stopping data transmission.

        print("   -> Expect: Watchdog should UNLOCK screen after 5 seconds...")
        # Print expected outcome: The application's safety watchdog should detect silence and unlock.

        # Stop sending data for 8 seconds (Exceeds Watchdog 5s limit)
        for i in range(8):
            # Loop 8 times (8 seconds).

            sys.stdout.write(f".")
            # Print a dot to visualize waiting.

            sys.stdout.flush()
            # Force the print buffer to output immediately.

            time.sleep(1)
            # Wait 1 second (no data is written to master_fd here!).

        print("\n   -> Watchdog test complete. Screen should be UNLOCKED now.")
        # End of watchdog test phase.

        # ==========================================
        # Phase 6: Exit Tunnel (Signal Restored)
        # ==========================================
        print(f"\n[{time.strftime('%H:%M:%S')}] [Phase 6] EXIT TUNNEL (Signal Restored 25 km/h)")
        # Print timestamp and phase description: Exiting tunnel, signal comes back.

        print("   -> Expect: Screen LOCK immediately")
        # Print expected outcome: Data returns at high speed, screen should lock again.

        for _ in range(5):
            # Loop 5 times.

            os.write(master_fd, NMEA_25_KPH)
            # Write 25 km/h data.

            time.sleep(1)
            # Wait 1 second.

        print("\n=== Test Sequence Completed ===")
        # Indicate end of all phases.

        print("Press Ctrl+C to exit and restore system.")
        # Instruction for the user to quit.

        while True:
            # Infinite loop to keep the script alive.

            time.sleep(1)
            # Sleep to reduce CPU usage while waiting for Ctrl+C.

    except OSError as e:
        # Catch operating system errors (like write failures).

        print(f"Write error: {e}")
        # Print the error message.

    finally:
        # The 'finally' block always runs, whether an error occurred or not.

        handle_signal(0, 0)
        # Call the signal handler to perform cleanup and exit.

if __name__ == "__main__":
    # Check if this script is being run directly (not imported as a module).

    main()
    # Call the main function.
