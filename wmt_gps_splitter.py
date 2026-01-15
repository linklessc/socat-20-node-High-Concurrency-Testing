#!/usr/bin/env python3
import os
import pty
import time
import signal
import sys
import fcntl
import glob
import re
import termios
import struct

# === Configuration ===
CONFIG_FILE = "/bin/wmt_winset/config.ini"
SOURCE_DEV = "/dev/ttyACM0"   # Physical GPS device path
START_ID = 0

# [Anti-Lag Threshold]
# Set to 4096 bytes (approx. 3-4 seconds of data volume)
# If the backlog exceeds this value, the buffer will be forcibly cleared to ensure real-time performance.
MAX_BUFFER_BACKLOG = 4096

# Global variable to track active ports for cleanup
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
                if line.strip().startswith("gps_port"):
                    parts = line.split('=')
                    if len(parts) > 1:
                        val = parts[1].strip().lower()
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
    Essential to prevent the script from freezing if an app stops reading.
    """
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def force_cleanup_at_startup():
    """
    Force Startup Cleanup:
    Scans for any existing /dev/gps* files and deletes them to ensure a clean state.
    """
    print("Performing startup cleanup...")
    file_list = glob.glob("/dev/gps*")
    
    for file_path in file_list:
        # Strict regex to avoid accidental deletion
        if re.match(r"^/dev/gps\d+$", file_path):
            try:
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
    Signal Handler for SIGTERM/SIGINT.
    """
    print(f"Received signal {signum}, exiting gracefully...")
    cleanup_symlinks(active_virtual_ports)
    sys.exit(0)

def main():
    # Register signal listeners
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # 1. Startup Cleanup
    force_cleanup_at_startup()

    end_id = load_config()
    if end_id <= 0:
        print("GPS Splitter disabled via config. Exiting.")
        sys.exit(0)

    print(f"=== Starting Python GPS Splitter (0 to {end_id}) ===")
    print(f"=== Anti-Lag Mode: Enabled (Threshold: {MAX_BUFFER_BACKLOG} bytes) ===")
    print(f"=== Data Mode: Smart Buffer Splicing (Residual Buffering) ===")

    # 2. Open the physical GPS source device
    retry_count = 0
    while not os.path.exists(SOURCE_DEV):
        print(f"Waiting for {SOURCE_DEV}...")
        time.sleep(2)
        retry_count += 1
        if retry_count > 30:
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
            master_fd, slave_fd = pty.openpty()
            
            # Set Master FD to non-blocking
            set_non_blocking(master_fd)
            
            slave_name = os.ttyname(slave_fd)
            link_name = f"/dev/gps{i}"

            if os.path.exists(link_name):
                try:
                    os.unlink(link_name)
                except OSError:
                    pass

            os.symlink(slave_name, link_name)
            os.chmod(link_name, 0o666)
            os.chmod(slave_name, 0o666)

            virtual_fds.append(master_fd)
            active_virtual_ports.append((master_fd, link_name))
            
            print(f" -> Created {link_name} connected to {slave_name}")
        except Exception as e:
            print(f"Error creating port {i}: {e}")

    print("=== Splitting Service Running... ===")

    # [RESIDUAL BUFFERING] Variable to hold incomplete data fragments
    leftover = b""

    # 4. Main Data Loop
    try:
        while True:
            try:
                # Read raw data (up to 4096 bytes)
                raw_data = os.read(source_fd, 4096)
                if not raw_data:
                    print("Source EOF")
                    break
                
                # === [METHOD 1: Residual Buffering Logic] ===
                # Combine leftover from previous read with new data
                data_to_process = leftover + raw_data

                # Find the position of the last newline character (\n)
                # GPS NMEA sentences always end with \n (0x0A)
                last_newline = data_to_process.rfind(b'\n')

                if last_newline != -1:
                    # We found at least one complete sentence.
                    # Extract everything up to the last newline as the payload to send.
                    final_payload = data_to_process[:last_newline+1]
                    
                    # Save the remaining part (fragment) for the next loop
                    leftover = data_to_process[last_newline+1:]
                else:
                    # No newline found in this entire chunk. 
                    # It means we only have a fragment. Keep it all and wait for more data.
                    leftover = data_to_process
                    continue # Skip writing this time

                # === Write complete sentences to all virtual ports ===
                for fd in virtual_fds:
                    try:
                        # [Anti-Lag Logic] Check backlog size
                        buf_info = fcntl.ioctl(fd, termios.TIOCOUTQ, struct.pack('I', 0))
                        pending_bytes = struct.unpack('I', buf_info)[0]

                        # If backlog is too high, flush the buffer
                        if pending_bytes > MAX_BUFFER_BACKLOG:
                            termios.tcflush(fd, termios.TCOFLUSH)
                            # Note: After flushing, we write 'final_payload' which contains
                            # complete NMEA sentences. This ensures the App gets valid data immediately.

                        # Write the complete NMEA sentences
                        os.write(fd, final_payload)

                    except BlockingIOError:
                        # Buffer full and couldn't flush (or just skipped), ignore to prevent blocking
                        pass
                    except OSError:
                        pass
            except OSError:
                break
    except Exception as e:
        print(f"Loop error: {e}")
    finally:
        cleanup_symlinks(active_virtual_ports)
        try:
            os.close(source_fd)
        except:
            pass

if __name__ == "__main__":
    main()
