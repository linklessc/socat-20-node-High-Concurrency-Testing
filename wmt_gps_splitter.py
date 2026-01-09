#!/usr/bin/env python3
import os
import pty
import time
import signal
import sys
import fcntl
import glob
import re

# === Configuration ===
CONFIG_FILE = "/bin/wmt_winset/config.ini"
SOURCE_DEV = "/dev/ttyACM0"  # Physical GPS device path
START_ID = 0

# Global variable to track active ports so the Signal Handler knows what to clean up
active_virtual_ports = []

def load_config():
    """
    Reads the configuration file to determine how many virtual GPS ports to open.
    Returns:
        int: The end ID (number of ports), or -1 if disabled/error.
    """
    end_id = -1
    if not os.path.exists(CONFIG_FILE):
        return -1
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                # Look for the 'gps_port' setting
                if line.strip().startswith("gps_port"):
                    parts = line.split('=')
                    if len(parts) > 1:
                        val = parts[1].strip().lower()
                        # If set to 'false', disable the splitter
                        if val == 'false':
                            return -1
                        try:
                            end_id = int(val)
                            return end_id
                        except ValueError:
                            return -1
    except Exception:
        pass
    return end_id

def set_non_blocking(fd):
    """
    Sets a File Descriptor (FD) to non-blocking mode.
    
    This is CRITICAL for preventing the 'deadlock' issue. 
    If a consumer (e.g., WinSet) reads slowly or not at all, 
    writing to this FD will raise a BlockingIOError instead of freezing the script.
    """
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def force_cleanup_at_startup():
    """
    [NEW] Force Startup Cleanup:
    Scans for any existing /dev/gps* files and deletes them.
    This ensures a clean state even if the previous instance crashed 
    or was killed without cleaning up.
    """
    print("Performing startup cleanup...")
    # Find all files matching /dev/gps*
    file_list = glob.glob("/dev/gps*")
    
    for file_path in file_list:
        # Strict regex match to ensure we only delete /dev/gps + numbers (e.g., /dev/gps0)
        # preventing accidental deletion of other devices.
        if re.match(r"^/dev/gps\d+$", file_path):
            try:
                # Check if it is a link or a file and remove it
                if os.path.islink(file_path) or os.path.exists(file_path):
                    os.unlink(file_path)
                    print(f" -> Removed stale file: {file_path}")
            except OSError as e:
                print(f"Error removing {file_path}: {e}")

def cleanup_symlinks(virtual_ports):
    """
    Removes the symlinks created during the current execution session.
    """
    print("Cleaning up active symlinks...")
    for _, link_name in virtual_ports:
        if os.path.exists(link_name):
            try:
                os.unlink(link_name)
                print(f"Removed {link_name}")
            except OSError as e:
                print(f"Error removing {link_name}: {e}")

def handle_sigterm(signum, frame):
    """
    Signal Handler for SIGTERM (sent by 'systemctl stop' or 'restart').
    Ensures graceful exit and cleanup of files.
    """
    print(f"Received signal {signum}, exiting gracefully...")
    cleanup_symlinks(active_virtual_ports)
    sys.exit(0)

def main():
    # Register signal listeners to catch termination signals
    signal.signal(signal.SIGTERM, handle_sigterm) # Systemctl stop
    signal.signal(signal.SIGINT, handle_sigterm)  # Ctrl+C

    # 1. [NEW] Perform forced cleanup immediately upon startup
    # This fixes the issue where residue files remained after a restart.
    force_cleanup_at_startup()

    end_id = load_config()
    if end_id <= 0:
        print("GPS Splitter disabled via config. Exiting.")
        sys.exit(0)

    print(f"=== Starting Python GPS Splitter (0 to {end_id}) ===")

    # 2. Open the physical GPS source device
    # Added a timeout/retry mechanism to wait for the hardware to be ready
    retry_count = 0
    while not os.path.exists(SOURCE_DEV):
        print(f"Waiting for {SOURCE_DEV}...")
        time.sleep(2)
        retry_count += 1
        if retry_count > 30: # Wait up to 60 seconds
             print(f"Device {SOURCE_DEV} not found after timeout.")
             sys.exit(1)
        
    try:
        # Open source in Read-Only mode, not controlling terminal
        source_fd = os.open(SOURCE_DEV, os.O_RDONLY | os.O_NOCTTY)
    except OSError as e:
        print(f"Failed to open source {SOURCE_DEV}: {e}")
        sys.exit(1)

    virtual_fds = []

    # 3. Create virtual Ports (PTY pairs)
    for i in range(START_ID, end_id):
        try:
            # Create a pseudo-terminal pair (master, slave)
            master_fd, slave_fd = pty.openpty()
            
            # Set Master FD to non-blocking (Essential!)
            set_non_blocking(master_fd)
            
            slave_name = os.ttyname(slave_fd)
            link_name = f"/dev/gps{i}"

            # Double-check cleanup just in case
            if os.path.exists(link_name):
                try:
                    os.unlink(link_name)
                except OSError:
                    pass

            # Create the symlink: /dev/gpsX -> /dev/pts/Y
            os.symlink(slave_name, link_name)
            
            # Set permissions so any user (WinSet) can read it
            os.chmod(link_name, 0o666)
            os.chmod(slave_name, 0o666)

            virtual_fds.append(master_fd)
            active_virtual_ports.append((master_fd, link_name))
            
            print(f" -> Created {link_name} connected to {slave_name}")
        except Exception as e:
            print(f"Error creating port {i}: {e}")

    print("=== Splitting Service Running... ===")

    # 4. Main Data Loop
    try:
        while True:
            try:
                # Read data from physical GPS
                data = os.read(source_fd, 4096)
                if not data:
                    print("Source EOF")
                    break
                
                # Write data to all virtual ports
                for fd in virtual_fds:
                    try:
                        os.write(fd, data)
                    except BlockingIOError:
                        # [ANNOTATION] Buffer is full (no one reading this port).
                        # We simply skip writing to prevent blocking the whole script.
                        pass 
                    except OSError:
                        # Handle other errors (e.g., port closed unexpectedly)
                        pass
            except OSError:
                break
    except Exception as e:
        print(f"Loop error: {e}")
    finally:
        # Final cleanup on exit
        cleanup_symlinks(active_virtual_ports)
        try:
            os.close(source_fd)
        except:
            pass

if __name__ == "__main__":
    main()